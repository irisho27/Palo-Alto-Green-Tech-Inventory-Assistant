Candidate Name: Iris Ho

Scenario Chosen: Green-Tech Inventory Assistant

Estimated Time Spent: 5 hours

Quick Start:
- Prerequisites:
Python 3.10+, `pip`, and optionally `GEMINI_API_KEY` for AI features.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

- Run Commands:

```bash
python app/web.py
```

Open `http://127.0.0.1:8000/`.

Optional CLI commands:

```bash
python app/main.py list
python app/main.py ask --query "what do i have?"
python app/main.py ask --query "bought two milks today"
python app/main.py chat --question "What should I reorder first?"
```

- Test Commands:

```bash
python -m unittest discover -s tests
python -m py_compile app/main.py app/inventory.py app/ai.py app/web.py
```

AI Disclosure:
- Did you use an AI assistant (Copilot, ChatGPT, etc.)? Yes, Copilot. 
- How did you verify the suggestions? The majority of my AI usage was concentrated within implementing the frontend which it did almost everything for. For the frontend, it would help me generate a UI, and I would look at it to make sure it looked how I wanted, before going in to make smaller changes. I also used AI to help write some of the backend logic; I asked it to write very specific things, then went in and reviewed the code manually before approving. 
- Give one example of a suggestion you rejected or changed: I initially accepted an AI suggestion to use a purely regex-based parser in the navigation box but changed it to call the backend /api/ask flow (LLM translation first and deterministic fallback second). This change was made after I tested a few things out on the frontend and realized the navigation box wouldn't accept natural phrasing from users. 

Tradeoffs & Prioritization:
- What did you cut to stay within the 4-6 hour limit? To stay within the 4-6 hour limit, I cut several things, including login screens and additional personalization to users--it would have been great to be able to adapt to the usages (e.g. cafe vs. office). The tradeoff I made for this was categories that each of the items would fit into (cafe supplies vs. office supplies). I also cut integration of AI to recognize items in photos, which would have made the addition of multiple items at the same time easier. 
- What would you build next if you had more time? I would add role-based accounts, photo-to-item ingestion with AI vision, stronger analytics (usage spike detection and smarter reorder recommendations), and richer sustainability dashboards with trend tracking over time. In addition to the photo-to-item ingestion, it would be great to implement a receipt-to-inventory process. There should also be more prompting for missing information. I would also add a daily digest feature sent through email based on the user's specific sustainability goals and inventory.
- Known limitations: The app uses a JSON file instead of a database, so it is not designed for concurrent multi-user workloads. Natural-language parsing still has edge cases with ambiguous phrasing. Forecasting quality depends on usage-history quality, and some advanced features currently rely on Gemini availability/quota.