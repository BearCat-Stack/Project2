const express = require('express');
const { GoogleGenerativeAI } = require('@google/generative-ai');
const { google } = require('googleapis');

const app = express();
app.use(express.json());
app.use(express.static('public'));

// --- Gemini Setup ---
const genAI = new GoogleGenerativeAI(process.env.GEMINI_API_KEY);
const model = genAI.getGenerativeModel({ model: 'gemini-2.0-flash' });

// --- Gmail OAuth2 Setup ---
const oauth2Client = new google.auth.OAuth2(
  process.env.GOOGLE_CLIENT_ID,
    process.env.GOOGLE_CLIENT_SECRET,
      process.env.GOOGLE_REDIRECT_URI
      );

      // Step 1: Redirect user to Google login
      app.get('/auth', (req, res) => {
        const url = oauth2Client.generateAuthUrl({
            access_type: 'offline',
                scope: ['https://www.googleapis.com/auth/gmail.send'],
                  });
                    res.redirect(url);
                    });

                    // Step 2: Google redirects back with auth code
                    app.get('/oauth2callback', async (req, res) => {
                      const { tokens } = await oauth2Client.getToken(req.query.code);
                        oauth2Client.setCredentials(tokens);
                          global.gmailTokens = tokens;
                            res.send('Gmail connected! You can close this tab.');
                            });

                            // --- Generate Email with Gemini ---
                            app.post('/generate', async (req, res) => {
                              try {
                                  const { linkedinData } = req.body;

                                      const prompt = `
                                      You are an expert outreach copywriter. Based on the LinkedIn profile data below, write:
                                      1. A compelling, personalized email SUBJECT LINE (max 10 words)
                                      2. A short, warm outreach email BODY (3-4 paragraphs max)

                                      Be specific - reference their actual role, company, and background. Sound human, not salesy.

                                      LinkedIn Data:
                                      ${linkedinData}

                                      You MUST respond with ONLY valid JSON in this exact format, no markdown, no extra text:
                                      {
                                        "subject": "...",
                                          "body": "..."
                                          }
                                          `;

                                              const result = await model.generateContent(prompt);
                                                  const text = result.response.text();

                                                      // Strip markdown code fences if Gemini adds them
                                                          const cleaned = text.replace(/```json|```/g, '').trim();
                                                              const parsed = JSON.parse(cleaned);

                                                                  res.json(parsed);
                                                                    } catch (err) {
                                                                        console.error(err);
                                                                            res.status(500).json({ error: 'Failed to generate email. Check your Gemini API key.' });
                                                                              }
                                                                              });

                                                                              // --- Send Email via Gmail ---
                                                                              app.post('/send', async (req, res) => {
                                                                                try {
                                                                                    const { to, subject, body } = req.body;

                                                                                        if (!global.gmailTokens) {
                                                                                              return res.status(401).json({ error: 'Not authenticated with Gmail. Visit /auth first.' });
                                                                                                  }

                                                                                                      oauth2Client.setCredentials(global.gmailTokens);
                                                                                                          const gmail = google.gmail({ version: 'v1', auth: oauth2Client });
                                                                                                          
                                                                                                              const message = [
                                                                                                                    `To: ${to}`,
                                                                                                                          'Content-Type: text/plain; charset=utf-8',
                                                                                                                                `Subject: ${subject}`,
                                                                                                                                      '',
                                                                                                                                            body,
                                                                                                                                                ].join('\n');
                                                                                                                                                
                                                                                                                                                    const encodedMessage = Buffer.from(message)
                                                                                                                                                          .toString('base64')
                                                                                                                                                                .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
                                                                                                                                                                
                                                                                                                                                                    await gmail.users.messages.send({
                                                                                                                                                                          userId: 'me',
                                                                                                                                                                                requestBody: { raw: encodedMessage },
                                                                                                                                                                                    });
                                                                                                                                                                                    
                                                                                                                                                                                        res.json({ success: true });
                                                                                                                                                                                          } catch (err) {
                                                                                                                                                                                              console.error(err);
                                                                                                                                                                                                  res.status(500).json({ error: 'Failed to send email.' });
                                                                                                                                                                                                    }
                                                                                                                                                                                                    });
                                                                                                                                                                                                    
                                                                                                                                                                                                    const PORT = process.env.PORT || 3000;
                                                                                                                                                                                                    app.listen(PORT, () => console.log(`Running on port ${PORT}`));
