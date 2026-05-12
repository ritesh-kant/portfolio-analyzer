AI Portfolio Analytics Platform — Lean MVP Plan

Goal

Build a simple portfolio analytics platform for Indian investors that:

* Connects with Groww and Zerodha
* Compares portfolio performance against index funds
* Shows tax harvesting opportunities
* Provides a simple portfolio health score
* Works well on mobile devices

This MVP should be small, fast to build, and easy to iterate on.

⸻

Core MVP Features

1. Portfolio Import

Supported Brokers

* Groww
* Zerodha

Data To Fetch

* Holdings
* Quantity
* Average buy price
* Current price
* Invested amount
* Current value

Notes

* No authentication system initially
* Single-user local usage is acceptable for MVP
* Store broker tokens/config locally or via env variables initially

⸻

2. Benchmark Comparison

Compare Portfolio Against

* Nifty 50 Index Fund

Show

* Portfolio return %
* Benchmark return %
* Difference %
* Portfolio current value
* Benchmark equivalent value

Goal

Help users answer:

“Am I actually beating index investing?”

⸻

3. Tax Harvest Dashboard

Show

Realized

* LTCG
* STCG

Unrealized

* Unrealized profits
* Unrealized losses

Tax Harvesting

* Remaining LTCG exemption
* Potential losses that can offset profits
* Holdings eligible for harvesting

⸻

Example UI

Tax-Free LTCG Used:
₹1,16,328 / ₹1,25,000
Remaining:
₹8,672

⸻

4. Portfolio Health Score

Score Categories

Diversification

Checks:

* sector concentration
* stock concentration

Benchmark Performance

Checks:

* underperforming or outperforming benchmark

Risk Concentration

Checks:

* too much capital in single stock/sector

⸻

Example

Portfolio Score: 72/100

⸻

Tech Stack

Frontend

Stack

* Next.js
* Tailwind CSS
* Zustand
* TypeScript

Requirements

* Mobile responsive
* Mobile-first UI
* Simple dashboard layout

⸻

Frontend Pages

/dashboard
/tax

⸻

Backend

Stack

* Node.js
* TypeScript
* AWS Lambda
* API Gateway

⸻

Services

/services
  /portfolio-service
  /analytics-service

⸻

Database

Database

* MongoDB

⸻

Collections

holdings
transactions
portfolio_snapshots

⸻

Infrastructure

Stack

* Terraform
* Serverless Framework
* AWS Cloud

⸻

Monorepo Structure

/apps
  /web
/services
  /portfolio-service
  /analytics-service
/packages
  /shared-types
  /broker-sdk
  /tax-core
  /analytics-core
/infrastructure
  /terraform

⸻

Development Plan

Week 1 — Foundation

Step 1 — Setup Monorepo

Setup:

* Turborepo
* PNPM
* TypeScript

⸻

Step 2 — Setup Frontend

Setup:

* Next.js
* Tailwind CSS
* Zustand

Create:

* dashboard page
* tax page

⸻

Step 3 — Setup Backend

Setup:

* Lambda handlers
* API Gateway
* local serverless development

⸻

Step 4 — Setup MongoDB

Create collections:

* holdings
* transactions
* portfolio_snapshots

⸻

Week 2 — Broker Integration

Step 5 — Zerodha Integration

Fetch:

* holdings
* positions

Normalize response structure.

⸻

Step 6 — Groww Integration

Fetch:

* holdings
* positions

Normalize response structure.

⸻

Step 7 — Create Shared Holding Model

Example:

type Holding = {
  symbol: string
  quantity: number
  averagePrice: number
  currentPrice: number
  investedAmount: number
  currentValue: number
  assetType: 'stock' | 'mf' | 'etf'
}

⸻

Week 3 — Dashboard & Benchmarking

Step 8 — Portfolio Dashboard

Show:

* invested amount
* current value
* total P&L
* benchmark comparison

⸻

Step 9 — Benchmark Engine

Compare:

* portfolio growth
* Nifty 50 growth

Use:

* CAGR or XIRR

⸻

Step 10 — Charts

Add charts:

* portfolio growth
* allocation
* benchmark comparison

Charts must work well on mobile.

⸻

Week 4 — Tax Engine

Step 11 — FIFO Tax Engine

Implement:

* FIFO calculation
* LTCG/STCG classification
* realized gains
* unrealized gains

⸻

Step 12 — Tax Dashboard

Show:

* realized LTCG
* realized STCG
* unrealized gains/losses
* remaining exemption
* profitable holdings
* loss-making holdings

⸻

Step 13 — Portfolio Score

Create basic scoring logic.

Factors:

* diversification
* concentration
* benchmark performance

⸻

Mobile Responsiveness Requirements

Must Support

* mobile devices first
* responsive charts
* responsive cards
* sticky summary section

⸻

Avoid

* huge desktop-only tables
* complex multi-column layouts

⸻

Important Rules

Tax Calculations

Tax calculations must be deterministic.

Never use AI for calculations.

⸻

Benchmarking

Benchmark calculations must:

* use same time period
* use correct portfolio cash flow timing

⸻

Scoring

Scoring must be explainable.

Avoid random scoring logic.

⸻

Out Of Scope For MVP

Do NOT build:

* Authentication
* Payments
* AI chatbot
* Mutual fund overlap analysis
* Notifications
* Real-time websocket updates
* SQS/SNS workflows
* Multi-user support
* Social/community features
* Options/F&O support
* Stock recommendations

⸻

Suggested Future Features

After MVP:

* AI insights
* Mutual fund overlap analysis
* Advanced risk metrics
* Tax optimization suggestions
* Multi-broker portfolio merging
* User authentication
* Subscription plans

⸻

Primary Success Metric

Users should immediately understand:

* whether they are beating index investing
* how much tax harvesting opportunity exists
* whether their portfolio is healthy

within 2 minutes of opening the app.