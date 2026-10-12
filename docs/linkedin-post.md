# LinkedIn post (draft)

Publish on Friday, October 9. Replace every [TODO] first, and keep only the claims that are
true for you today (for example, that you use it every week with your real data).

---

I asked Claude "Which ads should I turn off this week?" and it answered with my store's real
numbers. [TODO: keep this line only if you use it with real data]

I run a beauty e-commerce store in Ecuador. Our data lived in three places: our
database, Meta Ads Manager and our WhatsApp bot. And Meta never sees most of our sales. Every
Monday review took [TODO: X hours] of spreadsheets.

So I built an MCP server: a safe bridge between Claude and our database.

🔹 9 tools: sales, ads, stock, margins, customers, WhatsApp and data health
🔹 Read-only, enforced by PostgreSQL itself
🔹 Personal data never leaves: emails become hashes, names become initials

Three lessons:

1️⃣ Tool descriptions are the product. Claude chooses tools by reading them, so each one says
when to use it instead of another.
2️⃣ Put privacy in one place. If every tool must remember to hide data, one day one won't.
3️⃣ Trust your own numbers. Meta saw [TODO: X %] of the sales its ads produced.

Result: [TODO: your number].

The code is public, with synthetic data, so you can run it in 5 minutes: [TODO: link]
3-minute demo: [TODO: Loom link]

Next Friday: the WhatsApp bot that turns conversations into orders.

#MCP #Claude #AI #Ecommerce #Python
