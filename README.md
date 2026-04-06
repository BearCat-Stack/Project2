# Semi-Autonomous Finance Networking Outreach Assistant

This project implements a pipeline that:
1. Accepts a prospect via LinkedIn URL **or** Name + Company.
2. Uses Apollo (and optionally a LinkedIn proxy API) to gather verified contact and career data.
3. Produces fact-grounded background intelligence.
4. Generates a high-conversion, personalized cold email in elite finance networking style.
5. Applies quality control scoring and only accepts drafts rated **8/10+**.
6. Opens Gmail and composes (but does **not** send) the draft for human review.

## Tech Stack

- Python 3.10+
- `requests` for APIs
- Google Gemini API (`gemini-1.5-flash`) for structured summarization and writing
- Playwright for Gmail browser automation
- `python-dotenv` for `.env` loading

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
playwright install chromium
```

Copy env template and fill credentials:

```bash
cp .env.example .env
```

`.env` variables:

```bash
APOLLO_API_KEY=...
GEMINI_API_KEY=...
LINKEDIN_PROXY_API_KEY=...            # optional
LINKEDIN_PROXY_BASE_URL=...           # optional
```

The script calls `load_dotenv()` automatically at startup.

## Run

### Option A: LinkedIn URL input

```bash
python networking_assistant.py \
  --linkedin-url "https://www.linkedin.com/in/example-profile" \
  --sender-name "Your Name" \
  --sender-school "Baruch College" \
  --sender-major "Finance + Computer Information Systems" \
  --sender-role "Incoming Deutsche Bank Investment Banking Summer Analyst"
```

### Option B: Name + Company input

```bash
python networking_assistant.py \
  --name "Jane Smith" \
  --company "Evercore" \
  --sender-name "Your Name" \
  --sender-school "Baruch College" \
  --sender-major "Finance" \
  --sender-role "Incoming Deutsche Bank Investment Banking Summer Analyst"
```

### Optional flags

- `--no-linkedin-proxy`: disable LinkedIn proxy enrichment even if key is available.
- `--headless`: run browser automation headlessly.

## Output Contract

The script returns:
- Extracted Email Address
- Background Summary (2–3 sentences)
- Key Insights (2–3 bullets)
- 3 Subject Lines
- Final Email (90–130 words)
- QC score and rationale

## Notes on Non-Hallucination

- The prompt enforces strict evidence grounding.
- Personalization is conservative when evidence is sparse.
- Any draft under 8/10 QC triggers rejection.

## Gmail Automation Behavior

- Opens Gmail and clicks **Compose**.
- Fills recipient, subject, and body.
- Pauses for manual review.
- Never clicks send.
