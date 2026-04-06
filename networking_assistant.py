import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv
import google.generativeai as genai
from playwright.sync_api import sync_playwright


# -----------------------------
# Data Models
# -----------------------------


@dataclass
class ProspectInput:
    linkedin_url: Optional[str] = None
    name: Optional[str] = None
    company: Optional[str] = None

    def validate(self) -> None:
        if self.linkedin_url:
            return
        if self.name and self.company:
            return
        raise ValueError("Provide either --linkedin-url OR both --name and --company.")


@dataclass
class SenderProfile:
    name: str
    school: str
    major: str
    role: str


@dataclass
class CareerEvent:
    title: str
    company: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    description: Optional[str] = None


@dataclass
class ProspectProfile:
    full_name: str
    linkedin_url: Optional[str]
    email: Optional[str]
    current_role: Optional[str]
    current_company: Optional[str]
    experience: List[CareerEvent] = field(default_factory=list)
    source_evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DraftOutput:
    email_address: Optional[str]
    background_summary: str
    key_insights: List[str]
    subject_lines: List[str]
    final_email: str
    qc_score: int
    qc_rationale: str


# -----------------------------
# Providers: Apollo + LinkedIn Proxy
# -----------------------------


class ApolloClient:
    """
    Simple wrapper around Apollo People Enrichment API.
    Docs and payloads can vary by plan/version; adapt endpoint params as needed.
    """

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.apollo.io/v1"

    def enrich_person(
        self,
        linkedin_url: Optional[str] = None,
        name: Optional[str] = None,
        company: Optional[str] = None,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}/people/match"
        payload: Dict[str, Any] = {"api_key": self.api_key}

        if linkedin_url:
            payload["linkedin_url"] = linkedin_url
        if name:
            payload["name"] = name
        if company:
            payload["organization_name"] = company

        try:
            resp = requests.post(url, json=payload, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as exc:
            response_text = exc.response.text if exc.response is not None else "No response body"
            print("Apollo API request failed with an HTTP error.")
            print(f"Status code: {exc.response.status_code if exc.response is not None else 'unknown'}")
            print(f"Response: {response_text}")
            sys.exit(1)


class LinkedInProxyClient:
    """
    Generic LinkedIn profile fetcher using a third-party provider.
    Replace endpoint contract based on chosen vendor (Proxycurl, People Data Labs, etc.).
    """

    def __init__(self, api_key: str, base_url: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def get_profile(self, linkedin_url: str) -> Dict[str, Any]:
        resp = requests.get(
            f"{self.base_url}/linkedin/profile",
            params={"url": linkedin_url},
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()


# -----------------------------
# Parsing + Evidence Controls
# -----------------------------


def normalize_experience(raw_experience: List[Dict[str, Any]]) -> List[CareerEvent]:
    events: List[CareerEvent] = []
    for item in raw_experience or []:
        events.append(
            CareerEvent(
                title=item.get("title") or "",
                company=item.get("company") or item.get("organization_name") or "",
                start_date=item.get("start_date"),
                end_date=item.get("end_date"),
                description=item.get("description"),
            )
        )
    return events


def extract_verified_email(apollo_payload: Dict[str, Any]) -> Optional[str]:
    person = apollo_payload.get("person") or apollo_payload.get("contact") or {}
    email = person.get("email")
    if not email:
        return None

    # Conservative validity check.
    if re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return email
    return None


def merge_profile(
    input_data: ProspectInput,
    apollo_payload: Dict[str, Any],
    linkedin_payload: Optional[Dict[str, Any]],
) -> ProspectProfile:
    apollo_person = apollo_payload.get("person") or apollo_payload.get("contact") or {}
    full_name = (
        apollo_person.get("name")
        or (linkedin_payload.get("full_name") if linkedin_payload else None)
        or input_data.name
        or "Unknown"
    )

    linkedin_url = (
        input_data.linkedin_url
        or apollo_person.get("linkedin_url")
        or (linkedin_payload.get("linkedin_url") if linkedin_payload else None)
    )

    current_role = (
        (linkedin_payload or {}).get("headline")
        or apollo_person.get("title")
        or None
    )
    current_company = (
        (linkedin_payload or {}).get("current_company")
        or apollo_person.get("organization", {}).get("name")
        or input_data.company
    )

    experience_raw = (linkedin_payload or {}).get("experience") or apollo_person.get("employment_history") or []
    experience = normalize_experience(experience_raw)

    return ProspectProfile(
        full_name=full_name,
        linkedin_url=linkedin_url,
        email=extract_verified_email(apollo_payload),
        current_role=current_role,
        current_company=current_company,
        experience=experience,
        source_evidence={
            "apollo": apollo_payload,
            "linkedin": linkedin_payload or {},
        },
    )


# -----------------------------
# LLM Layer (fact-grounded)
# -----------------------------


SYSTEM_PROMPT = """You are a finance networking strategist.
Never invent facts. Use only supplied evidence JSON.
If evidence is weak, keep personalization conservative and explicit.
Return strict JSON only.
"""


def build_user_prompt(profile: ProspectProfile, sender: SenderProfile) -> str:
    evidence = {
        "full_name": profile.full_name,
        "linkedin_url": profile.linkedin_url,
        "email": profile.email,
        "current_role": profile.current_role,
        "current_company": profile.current_company,
        "experience": [e.__dict__ for e in profile.experience],
    }

    return f"""
TASK:
Create output fields:
1) background_summary: 2-3 sentences max.
2) key_insights: 2-3 bullet-style strings, fact-based only.
3) subject_lines: exactly 3 strings.
4) final_email: 90-130 words, finance networking tone, no em dashes.
5) qc: object with personalization_score, clarity_score, response_likelihood_score (1-10), overall_score (1-10), rationale.

Mandatory context for sender (must appear naturally in email intro):
- Name: {sender.name}
- School: {sender.school}
- Major: {sender.major}
- Incoming role: {sender.role}

Email constraints:
- Human, concise, sharp, respectful.
- Mention why recipient stood out based on exact career path evidence.
- Ask for 15-20 minute call and advice.
- Soft, subtle mention of interest in off-cycle/upcoming roles.
- No fluff and no generic praise.

If overall_score < 8, revise internally until overall_score >= 8.

EVIDENCE_JSON:
{json.dumps(evidence, indent=2)}

OUTPUT JSON SCHEMA:
{{
  "background_summary": "...",
  "key_insights": ["..."],
  "subject_lines": ["...", "...", "..."],
  "final_email": "...",
  "qc": {{
    "personalization_score": 0,
    "clarity_score": 0,
    "response_likelihood_score": 0,
    "overall_score": 0,
    "rationale": "..."
  }}
}}
"""


class OutreachWriter:
    def __init__(self, gemini_key: str, model: str = "gemini-1.5-flash"):
        genai.configure(api_key=gemini_key)
        self.model = genai.GenerativeModel(model)

    def generate(self, profile: ProspectProfile, sender: SenderProfile) -> DraftOutput:
        prompt = build_user_prompt(profile, sender)

        full_prompt = f"{SYSTEM_PROMPT}\n\n{prompt}"
        resp = self.model.generate_content(
            full_prompt,
            generation_config={"temperature": 0.7, "max_output_tokens": 1200},
        )
        text = (resp.text or "").strip()
        data = json.loads(text)

        email_words = len(data["final_email"].split())
        if not (90 <= email_words <= 130):
            raise ValueError(f"Email word count out of bounds: {email_words}")

        qc = data["qc"]
        if qc.get("overall_score", 0) < 8:
            raise ValueError("QC score below threshold after model revision.")

        return DraftOutput(
            email_address=profile.email,
            background_summary=data["background_summary"],
            key_insights=data["key_insights"],
            subject_lines=data["subject_lines"],
            final_email=data["final_email"],
            qc_score=qc["overall_score"],
            qc_rationale=qc.get("rationale", ""),
        )


# -----------------------------
# Gmail Automation (no auto-send)
# -----------------------------


def gmail_compose_draft(recipient: str, subject: str, body: str, headless: bool = False) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()

        page.goto("https://mail.google.com/", wait_until="load")
        print("Please complete login manually if needed.")

        page.wait_for_selector("div[gh='cm']", timeout=120000)
        page.click("div[gh='cm']")

        page.wait_for_selector("textarea[name='to']", timeout=30000)
        page.fill("textarea[name='to']", recipient)
        page.fill("input[name='subjectbox']", subject)
        page.fill("div[aria-label='Message Body']", body)

        print("Draft prepared in Gmail. Pausing for human review. Email NOT sent.")
        page.pause()
        browser.close()


# -----------------------------
# Orchestrator
# -----------------------------


def run_pipeline(
    prospect: ProspectInput,
    sender: SenderProfile,
    linkedin_proxy_enabled: bool = True,
) -> DraftOutput:
    prospect.validate()

    apollo_key = os.environ["APOLLO_API_KEY"]
    gemini_key = os.environ["GEMINI_API_KEY"]

    apollo_client = ApolloClient(apollo_key)
    apollo_payload = apollo_client.enrich_person(
        linkedin_url=prospect.linkedin_url,
        name=prospect.name,
        company=prospect.company,
    )

    linkedin_payload = None
    if linkedin_proxy_enabled and prospect.linkedin_url and os.getenv("LINKEDIN_PROXY_API_KEY"):
        proxy_key = os.environ["LINKEDIN_PROXY_API_KEY"]
        proxy_base = os.getenv("LINKEDIN_PROXY_BASE_URL", "https://api.exampleproxy.com/v1")
        linkedin_payload = LinkedInProxyClient(proxy_key, proxy_base).get_profile(prospect.linkedin_url)

    profile = merge_profile(prospect, apollo_payload, linkedin_payload)
    writer = OutreachWriter(gemini_key=gemini_key)
    output = writer.generate(profile, sender=sender)
    return output


def print_output(result: DraftOutput) -> None:
    print("\n=== OUTPUT ===")
    print(f"Extracted Email Address: {result.email_address or 'Not found'}")
    print("\nBackground Summary:")
    print(result.background_summary)
    print("\nKey Insights:")
    for insight in result.key_insights:
        print(f"- {insight}")
    print("\nSubject Lines:")
    for i, s in enumerate(result.subject_lines, start=1):
        print(f"{i}. {s}")
    print("\nFinal Email:\n")
    print(result.final_email)
    print(f"\nQC Score: {result.qc_score}/10")
    print(f"QC Rationale: {result.qc_rationale}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Semi-autonomous finance outreach assistant")

    parser.add_argument("--linkedin-url", dest="linkedin_url")
    parser.add_argument("--name")
    parser.add_argument("--company")

    parser.add_argument("--sender-name", required=True)
    parser.add_argument("--sender-school", required=True)
    parser.add_argument("--sender-major", required=True)
    parser.add_argument("--sender-role", required=True)

    parser.add_argument(
        "--no-linkedin-proxy",
        action="store_true",
        help="Disable LinkedIn proxy enrichment even if API key is set.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser automation in headless mode.",
    )

    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()

    prospect = ProspectInput(
        linkedin_url=args.linkedin_url,
        name=args.name,
        company=args.company,
    )
    sender = SenderProfile(
        name=args.sender_name,
        school=args.sender_school,
        major=args.sender_major,
        role=args.sender_role,
    )

    try:
        result = run_pipeline(
            prospect=prospect,
            sender=sender,
            linkedin_proxy_enabled=not args.no_linkedin_proxy,
        )
    except KeyError as exc:
        print(f"Missing required environment variable: {exc}")
        sys.exit(1)
    except ValueError as exc:
        print(f"Input/validation error: {exc}")
        sys.exit(1)

    print_output(result)

    if result.email_address:
        gmail_compose_draft(
            recipient=result.email_address,
            subject=result.subject_lines[0],
            body=result.final_email,
            headless=args.headless,
        )
    else:
        print("No verified email extracted. Skipping Gmail compose step.")


if __name__ == "__main__":
    main()
