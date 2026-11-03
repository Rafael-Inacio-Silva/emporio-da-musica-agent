# Empório da Música — Customer Service Agent

> *"Sua música começa aqui."*

## 1. Overview

Prototype of a text-message customer service agent for **Empório da Música**, a fictional musical instrument store in Campo Grande/MS. The agent resolves recurring questions on its own (opening hours, order status, product price and availability) by querying store data and applying the store policies, and hands off to the team only for exceptions such as complaints, returns or cases the policy does not cover.

Built for the Artefact AI Engineer technical challenge.

**Status:** step 1 of 6 (project structure and data handling).

## 2. How to run

Requirements: Python 3.11+ (developed on 3.14).

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # then fill in OPENAI_API_KEY
```

Run the tests:

```bash
pytest
```

Environment variables (`.env.example`):

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | — | OpenAI key. The account needs paid credit: the free tier does not support GPT-6 models. |
| `OPENAI_MODEL` | `gpt-6-luna` | Model used by the agent. |
| `OPENAI_REASONING_EFFORT` | `low` | Reasoning effort per call. |
| `REFERENCE_DATE` | empty | Optional ISO date that fixes "today" (see [Assumptions](#4-assumptions)). |

## 3. Technical decisions

### Data handling

- **pandas, loaded once at startup.** The dataset is small (under 200 rows), so an in-memory DataFrame is enough and keeps the project easy to run. In production the natural step is SQLite or Postgres, because stock changes in real time.
- **Fail fast on structural problems, clean and log content problems.** Missing columns, duplicated ids, unknown statuses, broken references between tables and unparseable dates raise `DataValidationError` at startup, so the agent never answers from data it cannot trust. Problems with a safe fix (a malformed tracking code) are cleaned and logged. *Alternative discarded:* silently dropping bad rows, which would hide data errors from the team.
- **Data minimization.** The customer phone number is not loaded at all, since no answer needs it (LGPD, policy 9).
- **Money as `float`, rounded on output.** Values have at most two decimals and calculations are simple percentages. *Alternative discarded:* `Decimal`, which adds conversion noise with no practical gain at this scale.

File renames: the original CSVs were named `desafio_tecnico_ai_eng - <table>.csv`. They were renamed to `categories.csv`, `customers.csv`, `order_items.csv`, `orders.csv`, `products.csv` and `promotions.csv` to avoid spaces in paths. Content was not changed. The policy manual keeps its original name, `data/políticas_da_loja.pdf`.

#### Data issues found and how they are handled

| # | Issue | Handling |
|---|---|---|
| 1 | No brand column; the brand only appears in the product name. | `brand` is extracted from the name with a fixed list of known brands (word boundaries, so "Kala" does not match "Kalani"). |
| 2 | Products 135–144: the name says one brand and the description another (e.g. "Music Man Bass 1X" described as a Fender Jazz Bass; "Korg Synth 1" described as an 88-key Roland while specs say 61 keys). | The **name** is treated as the source of truth. Descriptions are kept as they are; the conflict is a known limitation. |
| 3 | `specs` is JSON with inconsistent spacing and different keys per category. | Parsed into a `dict`. |
| 4 | Categories 6, 7 and 8 (woodwinds, brass, orchestral strings) have no products, although the policy says the store sells them. | Kept. The agent must not promise these items, since none are in the catalog. |
| 5 | Empty `tracking_code`, `estimated_delivery` and `notes` in 12 orders. | Expected: only `shipped` and `delivered` orders have tracking. Loaded as missing values, not errors. |
| 6 | Tracking codes must follow policy 5.3 (`BR` + 9 alphanumeric + `BR`). | Validated with a regex. All 8 codes in the data are valid. A malformed code would be discarded with a warning, because showing an invalid code is worse than saying it is unavailable. |
| 7 | Order totals that do not match current prices: order 3 (R$ 3,450 vs 2 × R$ 1,749 = R$ 3,498) and order 20 (R$ 1,400 vs R$ 1,488). | `total_brl` is kept as recorded: it is what the customer paid. Order totals are never recalculated from current prices. |
| 8 | Product 130 is `coming_soon` but appears in a delivered order (3) and a pending one (14). | Kept and documented. |
| 9 | Historical orders whose installments break the current rule 3.1 (orders 9, 16 and 18). | Kept as records of what happened. The rule is applied only to new quotes. |
| 10 | Products 93, 96 and 99 have two promotions each (all inactive). Promotions have no dates, only `is_active`. | `is_active` is converted to boolean. A check guarantees at most one active promotion per product, since promotions do not stack (policy 6.2). |
| 11 | Same e-mail for two customers (`bruno.martins@jmail.com`, ids 11 and 43) and several similar names. | Kept. Order verification uses order number + the e-mail of that order's customer, so it is still unambiguous. Customers are never identified by name. |
| 12 | E-mail case and spaces. | Normalized to lowercase without surrounding spaces. |
| 13 | Rows in `products.csv` are not sorted (product 145 appears before 131). | Sorted by `product_id`. |

Checked with no problems found: duplicated ids, references between tables (orders → customers, items → orders/products, promotions → products), date format, negative prices or stock, malformed e-mails.

## 4. Assumptions

- **Delivery date.** There is no actual delivery date in the data. For `delivered` orders, `estimated_delivery` is taken as the date the customer received the order.
- **Purchase channel.** There is no channel column. All orders in `orders.csv` are assumed to be online purchases, since they have delivery dates and tracking codes.
- **Active promotion.** Promotions have no start or end date, so "active" means `is_active = 1`.
- **Fictional e-mail domains.** Customer e-mails use made-up domains (`jmail.com`, `coldmail.com`, `inlook.com`, `hayoo.com.br`). They are kept exactly as provided, since order verification compares the e-mail the customer types with the stored one.
- **Reference date.** Orders go from 2025-10-15 to 2026-03-22. Deadlines such as the 7-day return window only make sense close to that period, so `REFERENCE_DATE` can fix "today" for demonstrations.

## 5. Testing

`pytest` covers the data loader: real data loads with the expected types, every product has a brand and category, the phone is not loaded, tracking codes follow policy 5.3, and broken files (missing file, duplicated id, unknown status, broken reference, two active promotions, bad date) are rejected.

## 6. Example conversations

*To be added in step 5.*

## 7. Known limitations and next steps

- Product descriptions 135–144 contradict the product names (see data issue 2).

## 8. AI coding assistant usage

*To be completed in step 6.*
