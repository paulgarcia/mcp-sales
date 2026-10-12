# Loom script: 3 minutes

Short sentences, easy to say. Read them, or say them in your own words.

**Before recording:** run `docker compose up --build`, connect Claude (see the README), and open
a new chat. Ask the questions in scene 3 once before recording, so you know what Claude will
answer.

---

## 1. Hook (0:00 to 0:15)

**Show:** Claude, with the question already typed.

> "This is Claude, connected to my store's system. I ask: which ads should I turn off this
> week? And it answers with numbers, not guesses. This copy runs on demo data, so I can show it
> publicly. Let me show you how it works."

## 2. The problem (0:15 to 0:45)

**Show:** the diagram in the README.

> "I run a beauty store in Ecuador. We sell through the web, a WhatsApp bot and sellers.
> Our numbers lived in three places: our database, Meta Ads Manager and the bot. And Meta does
> not see most of our sales. So every Monday I spent [X hours] building spreadsheets."

## 3. Demo (0:45 to 2:00)

**Show:** Claude. Ask three questions. Let the tool calls show on screen.

1. "Run the weekly review." (the `weekly_review` prompt)
   > "Claude checks first that the data is fresh. Then it reads sales, ads, stock and WhatsApp."
2. "Are we paying for ads on products we can't sell?"
   > "Here is one: a winning ad is still spending money on a product that is out of stock."
3. "What is our real margin, and our break-even ROAS?"
   > "It uses the real cost of every unit, then subtracts commissions and card fees."

## 4. Under the hood (2:00 to 2:35)

**Show:** `src/sales_mcp/privacy.py`, then one order in Claude's answer (initials and a hash).

> "Three things make this safe for a real database. One: the database user can only read.
> Two: personal data never leaves. Emails become hashes, names become initials. Three: every
> request needs a token, and there is a rate limit. 126 tests check all of this."

## 5. Result and next step (2:35 to 3:00)

**Show:** the repository on GitHub.

> "Result: [your number]. The code is public. The data is synthetic, so you can run it
> yourself. Next week: the WhatsApp bot that turns conversations into orders."
