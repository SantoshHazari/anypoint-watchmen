# Anypoint Partner Entitlement Governance Digest

## Objective

Build maximum usage control, guardrails, and governance inside Anypoint Platform for a partner `$0` presales org.

Primary constraint:

- Stay within included entitlement.
- Prevent accidental overage billing.
- Keep reporting current enough to identify consumption drift before it becomes financial exposure.

## Source Files Reviewed

- `C:\Downloads\FY26 MuleSoft Partner Entitlement FAQs (1) (1).pdf`
- `C:\Downloads\free-tier-usage-limits.txt`
- `C:\Downloads\anypoint_org_setup_so_far.md`

## Critical Finding

There are two entitlement views:

- FAQ baseline entitlement: generic partner entitlement values from the FAQ.
- Contract/capture entitlement: higher values from `free-tier-usage-limits.txt`.

Do not treat them as interchangeable. For operational guardrails, use the contract/capture values only after reconciling them with the actual Anypoint Usage Dashboard and signed order form.

## Reconciliation — CONFIRMED (2026-07-24)

The **vendor Monthly Usage Summary** (official invoice, Statement Period **Jun 2026**, source PDF: `resources/usage-summary-2026-06.pdf`) confirms that the **Contract/Capture Value** column below is the legally active entitlement — the FAQ baseline values were too low. `config/entitlements.json` has been updated to these confirmed values (`operating_mode: contract_confirmed`).

Confirmed entitlements from the invoice:

| Entitlement | Confirmed Limit | Used (Jun 2026) | % Used |
|---|---:|---:|---:|
| Mule Flows - Advanced | 280 | 17 | 6.07% |
| Mule Messages - Advanced | 47,000,000 | 347 | 0.00% |
| Data Throughput (GB) | 56,500 | 8 | 0.01% |
| API Manager Production | 52 | 0 | 0.00% |
| API Manager Pre-Production | 52 | 8 | 15.38% |
| APIs Under Governance | 52 | 3 | 5.77% |
| API Calls for Flex Gateway | 120,000,000 | 1,778 | 0.00% |
| Automation Credits 2.0 (IDP: 300,000 pages @ 30 credits/page) | 9,000,000 | 0 | 0.00% |
| API Access Requests | 100 | 0 | 0.00% |

**Not present on this invoice** (kept in config as `provisional_pending_removal`, to be deleted): Object Store Effective API Requests, Anypoint MQ API Requests.

## Consolidated Usage Limits

| Capability | FAQ Baseline | Contract/Capture Value | Overage Type | Cost Risk | Notes |
|---|---:|---:|---|---|---|
| Mule Flows | 200 | 280 | High watermark | High | High watermark means peak count matters, not only cumulative usage. |
| Mule Messages | 20,000,000 | 47,000,000 | Drawdown | High | Cumulative consumption bucket. |
| Data Throughput | 40,000 GB | 56,500 GB | Drawdown | High | FAQ says measured as Network Bytes. |
| API Manager Production | 10 | 52 | High watermark | High | Contract/capture also shows `API Manager Prod 40`; reconcile. |
| API Manager Pre-Production | 10 | 52 | High watermark | High | Contract/capture also shows `API Manager PreProd 40`; reconcile. |
| APIs Under Governance | 10 | 52 | High watermark | Medium/High | Contract/capture also shows `Anypoint API Governance 40`; reconcile. |
| Flex Gateway API Calls | 20,000,000 + 100,000,000 add-on mention | 120,000,000 | Drawdown | Medium/High | FAQ says Flex Gateway API calls limited to on-prem for one row; also mentions Managed Flex Gateway demo add-on. Reconcile scope. |
| Automation Credits 2.0 | 500,000 Integration package row; 9,000,000 Automation Advanced row | 9,000,000 | Drawdown | High | Credits are fungible across automation capabilities. |
| API Access Requests | Not found in extracted FAQ text | 100 | Unknown | Medium | From contract/capture file. Validate where surfaced in Usage Dashboard. |
| Object Store | 10 API requests/sec in FAQ; Object Store 100M in capture | Object Store 100M | Unknown | Medium | Need validate whether this is hard throttle, entitlement bucket, or both. |
| MQ API Requests | Not found in extracted FAQ text | MQ API Requests 500M | Unknown | Medium | Validate dashboard/report visibility. |
| Private Spaces | 2 Integration + 1 Automation row | 1 | High watermark likely | Medium | Contract/capture says 1; FAQ row says 2 for Integration package. Reconcile. |
| Network Connections | 4 Integration + 2 Automation row | 1 | High watermark likely | Medium | Contract/capture says 1; FAQ row says 4 for Integration package. Reconcile. |

## Overage Behavior

From the FAQ:

- Partners are responsible for monitoring their Anypoint usage.
- Exceeding entitlement creates overage.
- Overage is invoiced to partners at 100% list price.
- Two overage models exist:
  - High watermark: peak usage above entitlement.
  - Drawdown: cumulative consumption from an annual bucket.

Operational interpretation:

- High watermark controls require hard lifecycle discipline: do not create extra managed API instances, governed APIs, flows, spaces, or network connections casually.
- Drawdown controls require trend monitoring: daily/weekly burn rate, forecast to contract end date, and alerts at thresholds.

## Usage Reporting Locations

From the FAQ:

- Usage report is available in Anypoint Platform under the root org.
- Admin user is required to view the report.
- Report shows:
  - application names
  - environments
  - usage by business group
  - usage by application
- Daily usage tab is refreshed twice daily.
- Report can be exported as CSV.
- Data throughput is measured as Network Bytes.

FAQ navigation:

- Navigate to `Usage Dashboard`.
- Select `Mule Runtime` report type.
- Monitor `Daily Usage` reports.
- Use API Manager and API Governance Usage Dashboard for API/Governance tracking.
- Use IDP Usage Dashboard if IDP is enabled.

Related Anypoint Monitoring reports:

- `Monitoring > Reports`
- Reports include requests, performance, failures, CPU, and memory depending on deployment type.
- Reports can be exported as CSV.

## Alerting Strategy

Native alerting does not appear to cover every commercial entitlement directly. Use a layered model.

### Layer 1: Anypoint Monitoring Basic Alerts

Use for operational metrics that are available per app/API:

- Mule app message count
- Mule app message error count
- Mule app response time
- CPU utilization
- memory utilization
- API total request count
- API average response time
- API response codes
- API policy violations

Recommended baseline:

- Warning alert at abnormal message/request volume.
- Critical alert at known demo-safe traffic ceiling.
- Error/policy violation alerts for public-facing demo APIs.
- Alerts routed to platform admins and presales leads only.

### Layer 2: API Manager Alerts

Use API Manager/API Monitoring alerts for:

- API request count spikes
- response time issues
- response code anomalies
- policy violations
- contract lifecycle notifications

Cost-control value:

- Detect unexpected traffic.
- Detect exposed APIs being called after demo completion.
- Detect client apps using APIs outside approved demo windows.

### Layer 3: Manual or Automated Usage Dashboard Review

Required for entitlement-level tracking:

- flows
- messages
- data throughput
- managed API instances
- governed APIs
- automation credits
- Flex Gateway calls

Recommended process:

- Twice weekly during normal usage.
- Daily during active demos, enablement, or PoCs.
- Export CSV from Usage Dashboard.
- Maintain internal tracker with:
  - current usage
  - entitlement limit
  - percent used
  - remaining quantity
  - burn rate
  - projected exhaustion date
  - owner/business group/application attribution

### Layer 4: Administrative Stop Controls

Use procedural controls where platform alarms are insufficient:

- Stop or delete unused Runtime Manager apps.
- Manually delete unused/deprecated API Manager instances.
- Remove APIs from governance scope when no longer needed.
- Disable client app contracts after demos.
- Remove public endpoints unless actively used.

## Recommended Thresholds

| Threshold | Action |
|---:|---|
| 50% | Admin review; identify top consumers; confirm usage is intentional. |
| 70% | Presales lead review; freeze nonessential new deployments. |
| 80% | Require explicit admin approval for new APIs/apps/automation usage. |
| 90% | Stop noncritical apps/APIs; block new onboarding; review courtesy license or separate org option. |
| 95% | Emergency cleanup; shutdown unused workloads; escalate to AE/PAM if business need remains. |

For high watermark limits:

- Use lower internal soft caps.
- Example: if contractual managed API Prod is 52, operate internally at max 40 until reconciled.

For drawdown limits:

- Track annual runway, not only absolute percentage.
- Example: if 25% of contract time elapsed but 60% of messages consumed, escalate immediately.

## Guardrails To Build In Anypoint

### Access Management

- Keep `Root-Admins` to two named admins only.
- Use teams, not direct individual grants.
- Keep standard presales users away from root-level administration.
- Create separate teams:
  - `Org-Leads`
  - `Org-Users`
  - `Org-Observers`
- Require admin approval for permissions that can create billable footprint.

### Business Groups and Environments

- Use one child BG: `Example-Org`.
- Use demo-specific environments:
  - `demo-nonprod`
  - `demo-prod-like`
  - `training` only if needed
- Avoid naming anything `prod` without `demo-` prefix.
- Restrict deploy/create permissions by environment.

### Runtime Manager

- Do not leave demo apps running indefinitely.
- Require owner, demo purpose, expiry date, and cleanup status for every deployment.
- Stop apps after demo unless explicitly approved.
- Delete stale apps, not only stop them, if they contribute to high watermark or dashboard clutter.

### API Manager

- Manual deletion of unused API instances is required to prevent API Management overage.
- No unmanaged creation of API instances.
- Require:
  - API owner
  - business group
  - environment
  - demo expiry date
  - public/private exposure classification
  - client contract approval mode

### API Governance

- Governance should be selective at first.
- Do not place every experimental API under governance if governed API count is high watermark constrained.
- Apply governance to curated demo APIs and reusable presales assets.
- Maintain a register of governed APIs and review monthly.

### Exchange

- Restrict publish rights.
- Use naming conventions that identify demo assets.
- Avoid uncontrolled asset proliferation.
- Define visibility model:
  - private draft
  - internal presales
  - customer-demo-ready
  - deprecated

### Flex Gateway

- Treat public traffic as a primary cost risk.
- Require rate limiting on every demo API exposed through gateway.
- Require demo end-date cleanup for gateway routes.
- Track Flex Gateway API calls from Usage Dashboard.

### Automation / Composer / Flow Orchestration / RPA / IDP

- Automation Credits are fungible and can be consumed by several capabilities.
- Require approval before enabling high-volume automation demos.
- Use small sample datasets.
- Avoid loops, batch processing, and uncontrolled scheduled jobs.
- Track credits separately from Mule runtime usage.

## Operating Cadence

| Cadence | Activity |
|---|---|
| Daily during active demos | Check Usage Dashboard daily usage tab. |
| Twice weekly otherwise | Export usage CSV and update internal tracker. |
| Weekly | Review running apps, API Manager instances, governed APIs, Flex Gateway traffic. |
| Monthly | Reconcile entitlement dashboard with contract/order form. |
| Before every demo/PoC | Approve scope, expected usage, traffic source, cleanup date. |
| After every demo/PoC | Stop/delete apps, delete unused API instances, revoke client access. |

## Immediate Backlog

1. Reconcile entitlement numbers between FAQ, order form, and actual Anypoint Usage Dashboard.
2. Capture screenshots/export CSV from root org Usage Dashboard.
3. Define final permission matrix for:
   - `Root-Admins`
   - `Org-Leads`
   - `Org-Users`
   - `Org-Observers`
4. Create internal usage tracker from CSV export.
5. Define soft caps and escalation thresholds per entitlement.
6. Configure Anypoint Monitoring alerts for every active demo app/API.
7. Configure API Manager alerts for request spikes and policy violations.
8. Define mandatory cleanup workflow for apps, APIs, gateways, contracts, and governed APIs.
9. Define onboarding checklist and acceptable-use policy.
10. Define monthly governance review pack.

## Open Questions

- ~~Which exact entitlement quantities are legally active in the signed order form?~~ **RESOLVED (2026-07-24)** — confirmed by contract invoice; see Reconciliation section.
- ~~Are `API Manager Prod/PreProd` limits 40, 52, or split by package/add-on?~~ **RESOLVED** — 52 each.
- ~~Are `APIs under Governance` limits 40 or 52?~~ **RESOLVED** — 52.
- ~~Is Flex Gateway entitlement `20M`, `100M`, or `120M`, and does it differ by deployment model?~~ **RESOLVED** — 120,000,000.
- Are MQ and Object Store usage counts visible in the same root Usage Dashboard? (Not on the Jun 2026 invoice — likely not entitlements on this contract.)
- Is there an API for automated extraction of the entitlement usage report, or is CSV export manual only in this org?
- Which alerts can be created by API/UI versus which require manual reporting controls?
