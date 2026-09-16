# AssignMe — Full Pipeline Architecture
### Stakeholder Presentation Reference

> All numbers, thresholds, and logic below are derived directly from the production codebase.

---

## 1. End-to-End Pipeline Overview

```mermaid
flowchart TD
    classDef stage fill:#f9f0ff,stroke:#d6bcfa,stroke-width:2px,color:#444
    classDef logic fill:#ebf8ff,stroke:#90cdf4,stroke-width:2px,color:#444
    classDef db fill:#f1f5f9,stroke:#cbd5e1,stroke-width:2px,color:#444
    classDef engine fill:#e6fffa,stroke:#81e6d9,stroke-width:2px,color:#444
    classDef llm fill:#fefce8,stroke:#fde68a,stroke-width:2px,color:#444
    classDef enrichment fill:#fff7ed,stroke:#fed7aa,stroke-width:2px,color:#444
    classDef danger fill:#fff5f5,stroke:#fc8181,stroke-width:2px,color:#444

    A[Cron / Manual Run] --> B[Stage 1: watcher.py]:::stage
    B --> B1[Apify: Fetch LinkedIn QA job postings — USA]:::logic
    B1 --> B2[Normalize company name — strip legal suffixes]:::logic
    B2 --> B3{Valid company?}
    B3 -- No --> BX[Skip signal]
    B3 -- Yes --> B4[Create Company with LinkedIn enrichment]:::logic
    B4 --> B4a[website · industry · country · employee count · LinkedIn URL]:::logic
    B4a --> B4b[Save job poster as free Contact lead]:::logic
    B4b --> B5[(status = ENRICHED)]:::db

    B5 --> C[Stage 2: scorer.py — Rule-based, zero LLM]:::stage
    C --> C1{Hard disqualify?}
    C1 -- Staffing / Education / Government / Hospital --> CX[(status = REJECTED)]:::danger
    C1 -- No --> C2[Apply weighted scoring rules]:::logic
    C2 --> C3{Score >= threshold?}
    C3 -- Yes --> C4[(status = QUALIFIED)]:::db
    C3 -- No --> CX

    C4 --> D[Stage 3: finder.py]:::stage
    D --> D1[SearXNG search: company + target roles + LinkedIn]:::logic
    D1 --> D2[Ollama LLM: Extract best decision-maker]:::llm
    D2 --> D2a{Full name returned?}
    D2a -- Only first name --> DZ[Skip — need surname for Prospeo]:::logic
    D2a -- Yes --> D3[strip subdomains: careers. / jobs. / hire.]:::logic
    D3 --> D4["Prospeo /enrich-person API"]:::enrichment
    D4 --> D5{Prospeo found data?}
    D5 -- Yes --> D6[(status = CONTACT_FOUND)]:::db
    D5 -- No, NOT_FOUND --> D7[Save contact without email]:::logic

    D6 --> E[Stage 4: verifier.py]:::stage
    E --> E1{Source is Prospeo?}
    E1 -- Yes --> E2[(verified = PROSPEO_VERIFIED — skip re-check)]:::db
    E1 -- No --> E3[DNS MX record lookup]:::logic
    E3 --> E4{MX exists?}
    E4 -- Yes --> E5[(verified = PATTERN_ACCEPTED)]:::db
    E4 -- No --> E6[(verified = INVALID)]:::danger
    E2 --> E7[(status = EMAIL_VERIFIED)]:::db
    E5 --> E7

    E7 --> F[Stage 5: research.py]:::stage
    F --> F1[Load Prospeo enrichment context]:::enrichment
    F1 --> F2[Scrape company website — Playwright / HTTP fallback]:::logic
    F2 --> F3[Ollama LLM: Synthesize research brief]:::llm
    F3 --> F4[(status = RESEARCH_DONE)]:::db

    F4 --> G[Stage 6: email_writer.py]:::stage
    G --> G1[Ollama LLM: Generate 3-email sequence]:::llm
    G1 --> G2[Embed unique unsubscribe token per email]:::logic
    G2 --> G3[(3 × Email records — status = SCHEDULED)]:::db
    G3 --> G4[(status = EMAIL_READY)]:::db
    G4 --> G5[Dashboard: Human can review & edit]:::logic

    G5 --> H["Stage 7: sender.py → sending_orchestrator.py (13-step)"]:::engine
    H --> H1{Sent successfully?}
    H1 -- Yes --> H2[(status = EMAIL_SENT)]:::db
    H1 -- No --> H3[(Email FAILED or BLOCKED)]:::danger

    H2 --> J[Stage 8: reply_checker.py]:::stage
    J --> J1[Match In-Reply-To / References headers]:::logic
    J1 --> J2{Reply matched?}
    J2 -- Yes --> J3[(status = REPLIED)]:::db
    J2 -- No --> J4[No action — check again in 30min]:::logic
    J3 --> J5[(Cancel all SCHEDULED follow-ups)]:::db
```

---

## 2. ICP Scoring Rules (Stage 2 — scorer.py)

```mermaid
flowchart LR
    classDef pos fill:#f0fff4,stroke:#68d391,stroke-width:2px,color:#444
    classDef neg fill:#fff5f5,stroke:#fc8181,stroke-width:2px,color:#444
    classDef gate fill:#ebf8ff,stroke:#90cdf4,stroke-width:2px,color:#444

    subgraph Hard_Disqualification["🚫 Hard Disqualification (score = -200)"]
        direction TB
        HD1["Staffing & Recruiting"]:::neg
        HD2["Education / University"]:::neg
        HD3["Government / Military"]:::neg
        HD4["Hospital / Healthcare facilities"]:::neg
    end

    subgraph Weighted_Scoring["📊 Weighted Score"]
        direction TB
        WS1["+30 — Hiring engineers"]:::pos
        WS2["+20 — Tech / AI / Software industry"]:::pos
        WS3["+20 — 20–200 employees (sweet spot)"]:::pos
        WS4["+10 — USA / +5 Europe"]:::pos
        WS5["+5 — Has GitHub / tech presence"]:::pos
        WS6["-50 — Over 10,000 employees"]:::neg
        WS7["-20 — No website found"]:::neg
    end

    subgraph Result["Result"]
        R1{Score >= threshold?}:::gate
        R1 -- Yes --> R2["✅ QUALIFIED"]:::pos
        R1 -- No --> R3["❌ REJECTED"]:::neg
    end

    Hard_Disqualification --> Result
    Weighted_Scoring --> Result
```

---

## 3. Prospeo Enrichment Data (Stage 3 — finder.py)

```mermaid
flowchart TD
    classDef enrichment fill:#fff7ed,stroke:#fed7aa,stroke-width:2px,color:#444
    classDef db fill:#f1f5f9,stroke:#cbd5e1,stroke-width:2px,color:#444

    API["Prospeo /enrich-person API"]:::enrichment

    API --> P1["📧 Verified Email Address"]:::enrichment
    API --> P2["👤 Contact: headline · timezone · city · LinkedIn URL"]:::enrichment
    API --> P3["🏢 Company Intelligence"]:::enrichment

    P3 --> CI1["description_ai — AI-generated company summary"]:::enrichment
    P3 --> CI2["tech_stack — Confirmed technologies in use"]:::enrichment
    P3 --> CI3["active_job_titles — All current open positions"]:::enrichment
    P3 --> CI4["keywords — Company keywords & tags"]:::enrichment
    P3 --> CI5["funding_stage — Seed / Series A / B / C etc."]:::enrichment
    P3 --> CI6["revenue_range — Annual revenue bracket"]:::enrichment
    P3 --> CI7["is_b2b — B2B indicator flag"]:::enrichment

    P1 --> DB1[(Contact table — email + enrichment)]:::db
    P2 --> DB1
    CI1 --> DB2[(Company table — enrichment columns)]:::db
    CI2 --> DB2
    CI3 --> DB2
    CI4 --> DB2
    CI5 --> DB2
    CI6 --> DB2
    CI7 --> DB2

    CI1 --> SEED{description_ai AND tech_stack present?}
    CI2 --> SEED
    SEED -- Yes --> DB3[(Pre-seed Research record)]:::db
    SEED -- No --> SKIP[Skip — research.py will scrape from scratch]
```

---

## 4. The 13-Step Sending Orchestrator (Stage 7)

> Every single outbound email passes through this pipeline. No exceptions.

```mermaid
flowchart TD
    classDef check fill:#ebf8ff,stroke:#90cdf4,stroke-width:2px,color:#444
    classDef block fill:#fff5f5,stroke:#fc8181,stroke-width:2px,color:#444
    classDef send fill:#f0fff4,stroke:#68d391,stroke-width:2px,color:#444
    classDef engine fill:#e6fffa,stroke:#81e6d9,stroke-width:2px,color:#444

    START["📤 sender.py loads SCHEDULED emails where send_at <= now"] --> S0
    S0{Company already REPLIED?}
    S0 -- Yes --> CANCEL["❌ Email CANCELLED — reply already received"]:::block
    S0 -- No --> S1

    S1["Step 1: Resolve recipient contact + email"]:::check
    S1 --> S1a{Contact exists & has email?}
    S1a -- No --> B1["🚫 BLOCKED: NO_RECIPIENT_EMAIL"]:::block
    S1a -- Yes --> S2

    S2["Step 2: Validate recipient address"]:::check
    S2 --> S2a{Contact verified = INVALID?}
    S2a -- Yes --> B2["🚫 BLOCKED: CONTACT_INVALID"]:::block
    S2a -- No --> S3

    S3["Step 3: Campaign active check"]:::check
    S3 --> S3a{Campaign is_active = true?}
    S3a -- No --> B3["🚫 BLOCKED: CAMPAIGN_INACTIVE"]:::block
    S3a -- Yes --> S4

    S4["Step 4: Sending time check"]:::check
    S4 --> S4a["(Business hours logic — extensible)"]:::check
    S4a --> S5

    S5["Step 5: Full compliance check"]:::check
    S5 --> S5a["Check 4-layer suppression hierarchy"]:::check
    S5a --> S5b["Check unsubscribe token exists"]:::check
    S5b --> S5c["Check body length >= 10 chars"]:::check
    S5c --> S5d{Compliance approved?}
    S5d -- No --> B5["🚫 BLOCKED: SUPPRESSED / NO_TOKEN / EMPTY_BODY"]:::block
    S5d -- Yes --> S6

    S6["Step 6: Select eligible mailbox (rotation)"]:::engine
    S6 --> S6a["Round-robin by least-recently-sent timestamp"]:::engine
    S6a --> S6b["Skip: PAUSED / DISABLED / WARMUP_PENDING mailboxes"]:::engine
    S6b --> S6c["Skip: blocked domains (BLOCKED_SENDING_DOMAINS)"]:::engine
    S6c --> S6d["Skip: BLOCKED or DEGRADED domain health"]:::engine
    S6d --> S6e{Eligible mailbox found?}
    S6e -- No --> B6["⏸️ NO_ELIGIBLE_MAILBOX — retry later"]:::block
    S6e -- Yes --> S7

    S7["Step 7: Daily cap check"]:::check
    S7 --> S7a["sent_today vs. effective_daily_limit"]:::check
    S7a --> S7b{Under daily cap?}
    S7b -- No --> B7["⏸️ DAILY_CAP_REACHED"]:::block
    S7b -- Yes --> S8

    S8["Step 8: Hourly cap check"]:::check
    S8 --> S8a["sent_this_hour vs. daily_limit ÷ 8"]:::check
    S8a --> S8b{Under hourly cap?}
    S8b -- No --> B8["⏸️ HOURLY_CAP_REACHED"]:::block
    S8b -- Yes --> S9

    S9["Step 9: Domain aggregate limit"]:::check
    S9 --> S9a["check_domain_circuit_breakers()"]:::check
    S9a --> S9b{Domain OK?}
    S9b -- No --> B9["🚫 DOMAIN_CIRCUIT_BREAKER"]:::block
    S9b -- Yes --> S10

    S10["Step 10: Mailbox bounce circuit breaker"]:::check
    S10 --> S10a["hard_bounce_rate > 5%? bounce_rate > 10%?"]:::check
    S10a --> S11

    S11["Step 11: Mailbox spam circuit breaker"]:::check
    S11 --> S11a["spam_rate >= 0.3%? spam_rate >= 0.1%?"]:::check
    S11a --> S11b{All breakers clear?}
    S11b -- No --> B11["🚫 MAILBOX_CIRCUIT_BREAKER → auto-PAUSE"]:::block
    S11b -- Yes --> S12

    S12["Step 12: Build & send message"]:::send
    S12 --> S12a["Inject List-Unsubscribe header (RFC 8058)"]:::send
    S12a --> S12b["Inject List-Unsubscribe-Post: One-Click"]:::send
    S12b --> S12c["Append unsubscribe footer (plain + HTML)"]:::send
    S12c --> S12d["Thread follow-ups via In-Reply-To + References"]:::send
    S12d --> S12e{"Provider: Google OAuth → Gmail API or SMTP"}:::send
    S12e --> S12f{Send success?}
    S12f -- No --> FAIL["❌ FAILED — process_bounce if applicable"]:::block
    S12f -- Yes --> S13

    S13["Step 13: Record event + update counters"]:::engine
    S13 --> S13a["MessageEvent: type=SENT"]:::engine
    S13a --> S13b["SendingStats: increment hourly/daily counters"]:::engine
    S13b --> S13c["Mailbox: total_sent++, last_sent_at = now"]:::engine
    S13c --> S13d["Company: status = EMAIL_SENT"]:::engine
    S13d --> S13e["Auto-activate warmup if first send"]:::engine
    S13e --> DONE["✅ Email SENT — Message-ID stored"]:::send
```

---

## 5. Mailbox Warmup State Machine

> Mailboxes progress through a 7-state lifecycle. Transitions are automatic based on metrics.

```mermaid
stateDiagram-v2
    [*] --> WARMUP_PENDING: New mailbox added

    WARMUP_PENDING --> WARMUP_ACTIVE: Manual activation\nor first send

    WARMUP_ACTIVE --> ESTABLISHED: days >= 7\nAND sent >= 30\nAND bounce < 3%

    ESTABLISHED --> HEALTHY: days >= 21\nAND sent >= 150\nAND bounce < 2%\nAND spam < 0.1%

    ESTABLISHED --> DEGRADED: bounce > 5%\nOR spam >= 0.1%

    HEALTHY --> DEGRADED: bounce > 5%\nOR spam >= 0.1%

    DEGRADED --> PAUSED: bounce >= 10%\nOR spam >= 0.3%

    PAUSED --> RECOVERY: Manual admin resume

    RECOVERY --> ESTABLISHED: 7 days with\nbounce < 3%\nAND spam < 0.1%

    note right of WARMUP_PENDING: Cap: 0 emails/day\n(no sending)
    note right of WARMUP_ACTIVE: Cap: 5 emails/day\n(start slow)
    note right of ESTABLISHED: Cap: 20 emails/day\n(building reputation)
    note right of HEALTHY: Cap: 40 emails/day\n(full capacity)
    note left of DEGRADED: Cap: 8 emails/day\n(reduced volume)
    note left of PAUSED: Cap: 0 emails/day\n(all sending stopped)
    note left of RECOVERY: Cap: 8 emails/day\n(slow restart)
```

### Base Daily Caps by State

| State | Base Cap | Purpose |
|-------|----------|---------|
| `WARMUP_PENDING` | **0** | No sending — awaiting activation |
| `WARMUP_ACTIVE` | **5** | First week — prove deliverability |
| `ESTABLISHED` | **20** | Building consistent reputation |
| `HEALTHY` | **40** | Full capacity (further adjusted by metrics) |
| `DEGRADED` | **8** | Under investigation — reduced load |
| `PAUSED` | **0** | Circuit breaker fired — all sending halted |
| `RECOVERY` | **8** | Slow restart after admin resume |

---

## 6. Dynamic Daily Cap Calculation

> The base cap is NOT the final limit. It's multiplied by 4 dynamic factors.

```mermaid
flowchart LR
    classDef calc fill:#ebf8ff,stroke:#90cdf4,stroke-width:2px,color:#444
    classDef mult fill:#fefce8,stroke:#fde68a,stroke-width:2px,color:#444
    classDef result fill:#f0fff4,stroke:#68d391,stroke-width:2px,color:#444

    BASE["Base Cap\n(from warmup state)"]:::calc

    BASE --> M1
    subgraph M1["× Reputation Multiplier"]
        direction TB
        R1["Score ≥ 80 → ×1.3"]:::mult
        R2["Score ≥ 60 → ×1.0"]:::mult
        R3["Score ≥ 40 → ×0.7"]:::mult
        R4["Score ≥ 20 → ×0.4"]:::mult
        R5["Score < 20 → ×0.1"]:::mult
    end

    M1 --> M2
    subgraph M2["× Bounce Rate Penalty"]
        direction TB
        B1["Bounce ≥ 10% → ×0.0 (STOP)"]:::mult
        B2["Bounce ≥ 3% → ×0.3"]:::mult
        B3["Bounce ≥ 2% → ×0.7"]:::mult
        B4["Bounce < 2% → ×1.0"]:::mult
    end

    M2 --> M3
    subgraph M3["× Spam Rate Penalty (Google thresholds)"]
        direction TB
        S1["Spam ≥ 0.3% → ×0.0 (STOP)"]:::mult
        S2["Spam ≥ 0.1% → ×0.5"]:::mult
        S3["Spam < 0.1% → ×1.0"]:::mult
    end

    M3 --> M4
    subgraph M4["× Age Bonus"]
        direction TB
        A1["≥ 30 days → ×1.2"]:::mult
        A2["≥ 14 days → ×1.0"]:::mult
        A3["≥ 7 days → ×0.8"]:::mult
        A4["< 7 days → ×0.6"]:::mult
    end

    M4 --> FINAL["Effective Daily Limit\n= base × rep × bounce × spam × age"]:::result
    FINAL --> HOURLY["Hourly Limit = daily ÷ 8\n(min 1 per hour)"]:::result
```

### Example Calculation

A **HEALTHY** mailbox (base=40), reputation 85, bounce 0.5%, spam 0.02%, 45 days active:

```
40 × 1.3 × 1.0 × 1.0 × 1.2 = 62 emails/day
Hourly cap = 62 ÷ 8 = 7 emails/hour
```

A **WARMUP_ACTIVE** mailbox (base=5), reputation 50, bounce 0%, spam 0%, 3 days active:

```
5 × 1.0 × 1.0 × 1.0 × 0.6 = 3 emails/day
Hourly cap = max(1, 3 ÷ 8) = 1 email/hour
```

---

## 7. Circuit Breaker System

> Circuit breakers fire automatically. When triggered, they can reduce volume, pause a mailbox, or shut down an entire domain.

```mermaid
flowchart TD
    classDef warning fill:#fefce8,stroke:#fde68a,stroke-width:2px,color:#444
    classDef critical fill:#fff5f5,stroke:#fc8181,stroke-width:2px,color:#444
    classDef action fill:#e6fffa,stroke:#81e6d9,stroke-width:2px,color:#444

    subgraph Mailbox_Level["🔧 Mailbox-Level Circuit Breakers"]
        direction TB
        CB1["Hard bounce rate > 5%"]:::critical --> A1["→ PAUSE mailbox"]:::action
        CB2["Bounce rate ≥ 3%"]:::warning --> A2["→ Reduce volume (×0.3)"]:::action
        CB3["Bounce rate ≥ 10%"]:::critical --> A3["→ PAUSE mailbox"]:::action
        CB4["Spam rate ≥ 0.1%"]:::warning --> A4["→ Reduce volume 50%"]:::action
        CB5["Spam rate ≥ 0.3%"]:::critical --> A5["→ PAUSE mailbox + domain"]:::action
    end

    subgraph Domain_Level["🌐 Domain-Level Circuit Breakers"]
        direction TB
        DCB1["Domain spam rate ≥ 0.3%"]:::critical --> DA1["→ Domain status = DEGRADED\n→ All mailboxes on domain blocked"]:::action
        DCB2["DNS not verified"]:::critical --> DA2["→ Stop domain"]:::action
        DCB3["Domain status = BLOCKED"]:::critical --> DA3["→ Stop domain"]:::action
    end

    subgraph Auto_Recovery["♻️ Recovery Path"]
        direction TB
        REC1["Admin manually resumes PAUSED mailbox"]:::action
        REC1 --> REC2["Mailbox enters RECOVERY state (8/day)"]:::action
        REC2 --> REC3["7 days with bounce < 3% AND spam < 0.1%"]:::action
        REC3 --> REC4["Auto-promote to ESTABLISHED (20/day)"]:::action
    end
```

### Threshold Reference Table

| Metric | Warning | Critical | Action |
|--------|---------|----------|--------|
| Hard bounce rate | — | **> 5%** | Pause mailbox |
| Bounce rate | **≥ 3%** (×0.3 volume) | **≥ 10%** | Pause mailbox |
| Spam rate | **≥ 0.1%** (×0.5 volume) | **≥ 0.3%** | Pause mailbox + degrade domain |
| Domain spam rate | — | **≥ 0.3%** | Degrade domain (all mailboxes blocked) |

> **Why 0.3%?** Google's Postmaster Tools flags domains at 0.3% spam rate as "critical". Above this threshold, deliverability drops catastrophically and the domain risks permanent blacklisting.

---

## 8. 4-Layer Suppression Hierarchy (compliance.py)

> Checked in order. First match = BLOCKED. No email leaves the system if any layer triggers.

```mermaid
flowchart TD
    classDef check fill:#ebf8ff,stroke:#90cdf4,stroke-width:2px,color:#444
    classDef block fill:#fff5f5,stroke:#fc8181,stroke-width:2px,color:#444
    classDef pass fill:#f0fff4,stroke:#68d391,stroke-width:2px,color:#444

    EMAIL["Outbound email to: jane@acme.com"] --> L1

    L1["Layer 1: Global Email Suppression\nIs jane@acme.com in the global suppress list?"]:::check
    L1 -- Yes --> BLOCK["🚫 BLOCKED"]:::block
    L1 -- No --> L2

    L2["Layer 2: Global Domain Suppression\nIs acme.com domain-blocked?"]:::check
    L2 -- Yes --> BLOCK
    L2 -- No --> L3

    L3["Layer 3: Campaign-Level Suppression\nIs jane@acme.com suppressed for THIS campaign?"]:::check
    L3 -- Yes --> BLOCK
    L3 -- No --> L4

    L4["Layer 4: Account-Level Suppression\nIs jane@acme.com in account-wide suppress list?"]:::check
    L4 -- Yes --> BLOCK
    L4 -- No --> PASS

    PASS["✅ Compliance approved — proceed to send"]:::pass

    subgraph Suppression_Sources["How addresses get suppressed"]
        direction TB
        SRC1["🔴 HARD_BOUNCE — permanent, irreversible"]:::block
        SRC2["🔴 SPAM_COMPLAINT — permanent, irreversible"]:::block
        SRC3["🔴 INVALID_ADDRESS — permanent, irreversible"]:::block
        SRC4["🟡 UNSUBSCRIBED — admin-reversible"]:::check
        SRC5["🟡 DO_NOT_CONTACT — admin-reversible"]:::check
        SRC6["🟡 MANUAL_BLOCK — admin-reversible"]:::check
        SRC7["🟡 DOMAIN_BLOCKED — admin-reversible"]:::check
        SRC8["🟡 LEGAL_REQUEST — admin-reversible"]:::check
    end
```

### Permanent vs. Reversible Suppression

| Category | Reasons | Can be reversed? |
|----------|---------|-----------------|
| **Permanent** | `HARD_BOUNCE`, `SPAM_COMPLAINT`, `INVALID_ADDRESS` | ❌ Never — hard-coded protection |
| **Admin-reversible** | `UNSUBSCRIBED`, `DO_NOT_CONTACT`, `MANUAL_BLOCK`, `DOMAIN_BLOCKED`, `LEGAL_REQUEST` | ✅ Admin can unsuppress |

---

## 9. Unsubscribe Flow (RFC 8058 Compliant)

```mermaid
flowchart TD
    classDef server fill:#e6fffa,stroke:#81e6d9,stroke-width:2px,color:#444
    classDef action fill:#ebf8ff,stroke:#90cdf4,stroke-width:2px,color:#444
    classDef db fill:#f1f5f9,stroke:#cbd5e1,stroke-width:2px,color:#444

    subgraph Email_Headers["Headers injected into every email"]
        H1["List-Unsubscribe: <https://domain/unsubscribe/{token}>"]
        H2["List-Unsubscribe-Post: List-Unsubscribe=One-Click"]
        H3["Footer link: https://domain/unsubscribe/{token}"]
    end

    subgraph One_Click["Path A: Gmail One-Click (RFC 8058)"]
        OC1["Recipient clicks 'Unsubscribe' in Gmail UI"]:::action
        OC1 --> OC2["Gmail sends POST /unsubscribe/{token}"]:::action
        OC2 --> OC3["unsubscribe_server.py receives POST"]:::server
    end

    subgraph Manual["Path B: Manual Click"]
        MC1["Recipient clicks footer link"]:::action
        MC1 --> MC2["Browser loads GET /unsubscribe/{token}"]:::action
        MC2 --> MC3["unsubscribe_server.py serves confirmation page"]:::server
    end

    OC3 --> PROCESS
    MC3 --> PROCESS

    PROCESS["process_unsubscribe(token)"]:::server
    PROCESS --> P1["1. Find Email record by token"]:::action
    P1 --> P2["2. Resolve Contact email from Email.contact_id"]:::action
    P2 --> P3["3. Add to GLOBAL suppression list\n(reason=UNSUBSCRIBED, source=unsubscribe_link)"]:::db
    P3 --> P4["4. Cancel ALL SCHEDULED/DRAFT emails to this contact"]:::db
    P4 --> P5["Each cancelled email gets: status=CANCELLED,\nblocked_reason=RECIPIENT_UNSUBSCRIBED"]:::db

    subgraph Token_Design["🔒 Token Design"]
        T1["UUID-based — no email exposed in URL"]
        T2["Unique per email record (not per contact)"]
        T3["Token → Email → Contact → email address\n(indirect lookup only)"]
    end
```

---

## 10. Bounce & Complaint Processing

```mermaid
flowchart TD
    classDef event fill:#fefce8,stroke:#fde68a,stroke-width:2px,color:#444
    classDef action fill:#ebf8ff,stroke:#90cdf4,stroke-width:2px,color:#444
    classDef db fill:#f1f5f9,stroke:#cbd5e1,stroke-width:2px,color:#444
    classDef danger fill:#fff5f5,stroke:#fc8181,stroke-width:2px,color:#444

    subgraph Send_Outcomes["Every send has one outcome"]
        direction LR
        O1["✅ DELIVERED"]:::event
        O2["🔙 BOUNCED"]:::event
        O3["🚨 COMPLAINT"]:::event
        O4["📩 REPLY"]:::event
    end

    O2 --> BOUNCE
    subgraph BOUNCE["Bounce Processing (process_bounce)"]
        direction TB
        BT{Bounce type?}
        BT -- HARD --> HB1["Add recipient to GLOBAL suppression\n(reason=HARD_BOUNCE, permanent)"]:::danger
        HB1 --> HB2["Contact.verified = INVALID"]:::danger
        BT -- SOFT --> SB1["Record event only\n(temporary issue, may retry)"]:::action

        HB2 --> BU["Update Mailbox:\ntotal_bounced++\ntotal_hard_bounced++"]:::db
        SB1 --> BU
        BU --> BR["Recalculate mailbox health\n(bounce_rate, reputation_score)"]:::action
        BR --> BD["Recalculate domain health\n(aggregate across all domain mailboxes)"]:::action
        BD --> BCB["Check circuit breakers\n→ may auto-PAUSE"]:::danger
    end

    O3 --> COMPLAINT
    subgraph COMPLAINT["Complaint Processing (process_complaint)"]
        direction TB
        CP1["Add recipient to GLOBAL suppression\n(reason=SPAM_COMPLAINT, permanent)"]:::danger
        CP1 --> CP2["Cancel ALL pending emails to this contact"]:::action
        CP2 --> CP3["Update Mailbox:\ntotal_complaints++"]:::db
        CP3 --> CP4["Recalculate mailbox health"]:::action
        CP4 --> CP5["Recalculate domain health"]:::action
        CP5 --> CP6["Check circuit breakers\n→ likely auto-PAUSE at 0.3%"]:::danger
    end

    O4 --> REPLY
    subgraph REPLY["Reply Processing (record_reply)"]
        direction TB
        RP1["Record REPLY event"]:::action
        RP1 --> RP2["Mailbox: total_replies++"]:::db
        RP2 --> RP3["Recalculate health\n(replies improve reputation)"]:::action
    end
```

---

## 11. Reputation Score Formula

> Our internal operational score (0–100). Higher = healthier mailbox.

```mermaid
flowchart TD
    classDef pos fill:#f0fff4,stroke:#68d391,stroke-width:2px,color:#444
    classDef neg fill:#fff5f5,stroke:#fc8181,stroke-width:2px,color:#444
    classDef base fill:#ebf8ff,stroke:#90cdf4,stroke-width:2px,color:#444

    BASE["Base score: 50"]:::base

    BASE --> D["+ Delivery Rate (0 to +25)\ndelivered/sent × 25"]:::pos
    D --> B["- Bounce Rate Penalty (0 to -30)\n≥10%: −30 · ≥5%: −20 · ≥3%: −10 · ≥1%: −5"]:::neg
    B --> S["- Spam Complaint Penalty (0 to -40)\n≥0.3%: −40 · ≥0.1%: −25 · >0: −5"]:::neg
    S --> R["+ Reply Rate Bonus (0 to +15)\n≥10%: +15 · ≥5%: +10 · ≥2%: +5"]:::pos
    R --> A["+ Account Age Bonus (0 to +10)\n≥30 days: +10 · ≥14 days: +5"]:::pos
    A --> FINAL["Final Score: clamp(0, 100)"]:::base
```

### Score Range Interpretation

| Score | Meaning | Effect on Daily Cap |
|-------|---------|-------------------|
| **80–100** | Excellent — strong deliverability | ×1.3 multiplier |
| **60–79** | Good — normal operations | ×1.0 multiplier |
| **40–59** | Fair — needs monitoring | ×0.7 multiplier |
| **20–39** | Poor — significant issues | ×0.4 multiplier |
| **0–19** | Critical — near total block | ×0.1 multiplier |

---

## 12. Domain Health Aggregation

```mermaid
flowchart TD
    classDef mailbox fill:#ebf8ff,stroke:#90cdf4,stroke-width:2px,color:#444
    classDef domain fill:#e6fffa,stroke:#81e6d9,stroke-width:2px,color:#444
    classDef db fill:#f1f5f9,stroke:#cbd5e1,stroke-width:2px,color:#444

    subgraph Domain_acme["Domain: acme.com"]
        direction TB
        MB1["sender1@acme.com\nsent=200 · bounced=4 · complaints=0"]:::mailbox
        MB2["sender2@acme.com\nsent=150 · bounced=2 · complaints=1"]:::mailbox
        MB3["sender3@acme.com\nsent=50 · bounced=1 · complaints=0"]:::mailbox
    end

    MB1 --> AGG
    MB2 --> AGG
    MB3 --> AGG

    AGG["Aggregate Domain Health"]:::domain
    AGG --> D1["total_sent = 200 + 150 + 50 = 400"]:::db
    AGG --> D2["bounce_rate = (4+2+1) / 400 = 1.75%"]:::db
    AGG --> D3["spam_rate = (0+1+0) / 400 = 0.25%"]:::db
    AGG --> D4["reputation = avg(rep1, rep2, rep3)"]:::db

    D3 --> CHECK{spam_rate >= 0.3%?}
    CHECK -- Yes --> DEGRADE["⚠️ Domain DEGRADED\nAll mailboxes on domain blocked"]
    CHECK -- No --> OK["✅ Domain healthy"]
```

---

## 13. Provider Adapter Architecture

```mermaid
flowchart TD
    classDef base fill:#f1f5f9,stroke:#cbd5e1,stroke-width:2px,color:#444
    classDef smtp fill:#ebf8ff,stroke:#90cdf4,stroke-width:2px,color:#444
    classDef google fill:#fefce8,stroke:#fde68a,stroke-width:2px,color:#444
    classDef error fill:#fff5f5,stroke:#fc8181,stroke-width:2px,color:#444

    ORCH["sending_orchestrator.py"] --> FACTORY["get_provider(mailbox)\nFactory pattern"]:::base

    FACTORY --> DECIDE{mailbox.provider?}
    DECIDE -- smtp --> SMTP["SMTPProvider"]:::smtp
    DECIDE -- google --> GOOGLE["GoogleWorkspaceProvider"]:::google

    subgraph SMTP_Flow["SMTP Provider"]
        direction TB
        SF1["Build MIME message\n(multipart/alternative)"]:::smtp
        SF1 --> SF2["Inject compliance headers:\nMessage-ID · In-Reply-To · References\nList-Unsubscribe · List-Unsubscribe-Post"]:::smtp
        SF2 --> SF3{Port?}
        SF3 -- 465 --> SF4["SMTP_SSL (implicit TLS)"]:::smtp
        SF3 -- 587/other --> SF5["SMTP + STARTTLS"]:::smtp
        SF4 --> SF6["server.login() → server.sendmail()"]:::smtp
        SF5 --> SF6
    end

    subgraph Google_Flow["Google Workspace Provider"]
        direction TB
        GF1{OAuth connected?}:::google
        GF1 -- No --> GF2["Fallback to SMTP\n(smtp.gmail.com:465)"]:::smtp
        GF1 -- Yes --> GF3["Refresh access token\nfrom oauth_refresh_token"]:::google
        GF3 --> GF4["Gmail API: messages.send()"]:::google
    end

    subgraph Error_Handling["Error Classification"]
        direction TB
        E1["SMTPRecipientsRefused → HARD bounce"]:::error
        E2["SMTPDataError 5xx → HARD bounce"]:::error
        E3["SMTPDataError 4xx → SOFT bounce"]:::error
        E4["SMTPAuthenticationError → Permanent fail"]:::error
        E5["Other SMTPException → SOFT bounce"]:::error
    end
```

---

## 14. Mailbox Rotation Algorithm

```mermaid
flowchart TD
    classDef step fill:#ebf8ff,stroke:#90cdf4,stroke-width:2px,color:#444
    classDef skip fill:#fff5f5,stroke:#fc8181,stroke-width:2px,color:#444
    classDef select fill:#f0fff4,stroke:#68d391,stroke-width:2px,color:#444

    START["Load all mailboxes\nORDER BY last_sent_at ASC NULLS FIRST"]:::step

    START --> F1{"is_active = true?"}
    F1 -- No --> SKIP1["Skip"]:::skip
    F1 -- Yes --> F2

    F2{"status ≠ PAUSED and ≠ DISABLED?"}
    F2 -- No --> SKIP2["Skip"]:::skip
    F2 -- Yes --> F3

    F3{"warmup_status ≠ WARMUP_PENDING and ≠ PAUSED?"}
    F3 -- No --> SKIP3["Skip"]:::skip
    F3 -- Yes --> F4

    F4{"Domain NOT in BLOCKED_SENDING_DOMAINS?"}
    F4 -- No --> SKIP4["Skip — domain blocked"]:::skip
    F4 -- Yes --> F5

    F5{"Domain health ≠ BLOCKED and ≠ DEGRADED?"}
    F5 -- No --> SKIP5["Skip — domain health bad"]:::skip
    F5 -- Yes --> F6

    F6["get_mailbox_policy() → can_send?"]:::step
    F6 -- No --> SKIP6["Skip — daily/hourly cap reached"]:::skip
    F6 -- Yes --> SELECTED["✅ Selected! This mailbox sends the next email"]:::select

    SKIP1 --> NEXT["→ Try next mailbox in round-robin order"]
    SKIP2 --> NEXT
    SKIP3 --> NEXT
    SKIP4 --> NEXT
    SKIP5 --> NEXT
    SKIP6 --> NEXT
    NEXT --> F1
```

> **Round-robin strategy:** Mailboxes are sorted by `last_sent_at ASC NULLS FIRST` — the mailbox that hasn't sent in the longest time goes first. This naturally distributes volume evenly across all healthy mailboxes without complex weighting algorithms.

---

## 15. Scheduler (Production Automation)

```mermaid
flowchart LR
    classDef sched fill:#f9f0ff,stroke:#d6bcfa,stroke-width:2px,color:#444

    SCHEDULER["scheduler.py\n(APScheduler)"]:::sched

    SCHEDULER --> W["watcher.py\nevery 6 hours"]:::sched
    SCHEDULER --> SC["scorer.py\nevery 2 hours"]:::sched
    SCHEDULER --> FI["finder.py\nevery 2 hours"]:::sched
    SCHEDULER --> VE["verifier.py\nevery 2 hours"]:::sched
    SCHEDULER --> RE["research.py\nevery 2 hours"]:::sched
    SCHEDULER --> EW["email_writer.py\nevery 2 hours"]:::sched
    SCHEDULER --> SE["sender.py\nevery 1 hour"]:::sched
    SCHEDULER --> RC["reply_checker.py\nevery 30 minutes"]:::sched
    SCHEDULER --> HE["health recalc\nperiodically"]:::sched
```

> Each stage runs as an isolated subprocess — one crash doesn't take down the pipeline.
