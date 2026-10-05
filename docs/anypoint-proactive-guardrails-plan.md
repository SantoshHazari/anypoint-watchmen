# Anypoint Proactive Guardrails Plan

## Current State

- Usage is zero.
- No users.
- No apps.
- No APIs.
- No Exchange assets.
- No API Manager instances.
- No governed APIs.
- Automated rate limiting policy already applied to all environments at `50 requests/min`.

This is the right point to establish controls before self-service begins.

## Priority 1: Root Org Controls

### Confirm usage-report access

Action:

- Grant `Usage Viewer` at root org only to platform admins and the future reporting automation identity.
- Verify root org Usage Reports are visible.
- Confirm the following products appear:
  - Mule Runtime
  - API Manager
  - API Governance
  - Flex Gateway / Omni Gateway if available
  - Anypoint MQ
  - Object Store v2
  - IDP if enabled

Reason:

- Usage Reports are available only from the root organization.
- Usage Viewer must be assigned at root org level.
- Reports can be exported as CSV and are also available through Anypoint Usage API.

Reference:

- https://docs.mulesoft.com/general/usage-reports

### Validate billing alert recipients

Action:

- Confirm the primary billing contact email.
- Make it a monitored shared mailbox or distribution list, not an individual-only mailbox.
- Add internal process to review billing threshold emails immediately.

Reason:

- MuleSoft sends automatic usage alerts at 80% and 90% of contractual entitlement to the primary billing contact.
- These are monthly after threshold is reached, so they are not enough for operational prevention.

Reference:

- https://docs.mulesoft.com/general/usage-reports

## Priority 2: Usage API Automation

### Create least-privilege automation identity

Action:

- Create a connected app for usage reporting.
- Use client credentials if the required Usage API scope is available.
- Assign only the scopes/permissions required for usage read access.
- Store client ID/secret outside Anypoint and rotate on a defined cadence.

Reason:

- Connected apps support automation scenarios using OAuth client credentials.
- Usage API requires `Usage Viewer`.

References:

- https://docs.mulesoft.com/access-management/connected-apps-overview
- https://docs.mulesoft.com/access-management/creating-connected-apps-dev
- https://anypoint.mulesoft.com/exchange/portals/anypoint-platform/f1e97bc6-315a-4490-82a7-23abe036327a/usage-api/

### Build daily usage extraction

Action:

- Query Anypoint Usage API daily.
- Pull both daily and monthly usage.
- Persist snapshots outside Anypoint.
- Keep raw data immutable.
- Calculate:
  - current usage
  - entitlement limit
  - percent consumed
  - remaining quantity
  - daily burn rate
  - 7-day burn rate
  - projected exhaustion date
  - top business groups/apps/environments

Reason:

- Usage data is refreshed twice daily.
- The most recent three days can still change.
- Daily retention is three months; monthly retention is up to five years.

Reference:

- https://docs.mulesoft.com/general/usage-reports

### Create internal alerts from usage data

Action:

- Alert internally at:
  - 25% unexpected consumption
  - 50% admin review
  - 70% presales lead review
  - 80% freeze nonessential creation
  - 90% cleanup/escalation
  - 95% emergency shutdown
- Add burn-rate alerts:
  - projected exhaustion before contract end
  - consumption spike over previous 7-day baseline
  - any usage in a product expected to remain unused

Reason:

- Platform usage alerts are too coarse for day-to-day governance.
- Cost prevention needs proactive burn-rate monitoring.

## Priority 3: Native Platform Alerts

### Configure Anypoint Monitoring alert templates

Action:

- Define standard alert templates for every future Mule app:
  - message count spike
  - error count
  - response time
  - CPU
  - memory
- Define standard alert templates for every future API:
  - total requests
  - response time
  - response codes
  - policy violations

Reason:

- Anypoint Monitoring supports basic alerts for servers, Mule apps, and APIs.
- Thresholds are evaluated every five minutes.
- Integration Advanced has alert allocation limits, so templates must be selective.

Reference:

- https://docs.mulesoft.com/monitoring/alerts-us-eu

### Treat operational alerts as abuse indicators

Action:

- Request spike alert: detect unexpected traffic.
- Policy violation alert: detect unauthorized consumers.
- Error spike alert: detect broken clients retrying aggressively.
- CPU/memory alert: detect runaway demo workloads.

Reason:

- These alerts do not directly measure entitlement burn.
- They are still useful early-warning signals for consumption problems.

## Priority 4: Automated API Policies

### Keep environment-level rate limiting

Current control:

- Automated rate limiting policy: `50 requests/min`.

Recommended additions:

- Client ID enforcement for all non-public APIs.
- SLA-based rate limiting where client contracts are used.
- Spike control if available and compatible.
- Header injection/correlation ID policy for traceability.
- Standard error handling policy if supported by your gateway/runtime pattern.

Reason:

- Automated policies apply consistently to APIs in an environment.
- They reduce dependency on manual policy attachment.
- Automated policies take precedence over the same policy type applied manually.

Important limitation:

- MuleSoft docs state alerts cannot be added to API instances protected by automated policies. Validate this behavior in your tenant before relying on both together.

Reference:

- https://docs.mulesoft.com/mule-gateway/policies-automated-overview

## Priority 5: Audit and Change Detection

### Enable audit review process

Action:

- Assign `Audit Log Viewer` to admins/observers who need visibility.
- Review audit logs for:
  - user creation
  - permission changes
  - connected app creation
  - Exchange asset creation/publication
  - API Manager instance creation/deletion
  - API Governance profile changes
  - Runtime Manager deployments
  - MQ/Object Store resource creation
- Export audit logs periodically.

Reason:

- Audit logs track user and platform actions.
- Audit Log Query API is available.
- Default retention is one year unless configured differently.

Reference:

- https://docs.mulesoft.com/access-management/audit-logging

### Build admin-change alerting

Action:

- Use Audit Log Query API or exported audit logs to detect:
  - any new user
  - any new root-level permission
  - any new connected app
  - any API Manager instance
  - any deployment
  - any API Governance profile activation
  - any external access/trusted domain change

Reason:

- Consumption control starts with change control.
- With usage at zero, every new object should be intentional and attributable.

## Priority 6: Preventive Access Model

### Lock down creation permissions

Action:

- Standard users should not create:
  - business groups
  - environments
  - connected apps
  - API Manager instances without approval
  - governed API profiles
  - public Exchange assets
  - Runtime Manager deployments
  - Flex Gateway registrations/routes
  - MQ queues/exchanges
  - Object Store resources

Recommended team posture:

- `Root-Admins`: full root administration, two users only.
- `Org-Leads`: approve and manage controlled demos.
- `Org-Users`: build only inside approved scope.
- `Org-Observers`: read-only, plus audit/usage visibility if needed.

## Priority 7: Object Lifecycle Controls

### Require metadata for every created object

Action:

- Require naming convention:
  - owner
  - purpose
  - environment
  - expiry date
  - customer/demo reference if applicable
- Maintain an inventory for:
  - apps
  - APIs
  - API Manager instances
  - governed APIs
  - Exchange assets
  - client apps/contracts
  - Flex Gateway APIs/routes
  - MQ/Object Store resources

Reason:

- High watermark entitlements are vulnerable to forgotten objects.
- Drawdown entitlements are vulnerable to forgotten running workloads.

### Define cleanup SLA

Action:

- Every demo/PoC gets a cleanup date.
- Cleanup includes:
  - stop/delete app
  - delete unused API Manager instance
  - remove or deprecate Exchange asset
  - revoke client app contracts
  - remove API from governance if no longer needed
  - delete unused MQ/Object Store resources

## Recommended Next Work Items

1. Create root Usage Viewer access model.
2. Validate primary billing contact and usage threshold recipients.
3. Open Usage Reports and record the empty baseline.
4. Create connected app for Usage API testing.
5. Prototype Usage API extraction.
6. Define entitlement limits file in machine-readable form.
7. Implement usage threshold calculations.
8. Define native alert templates.
9. Define audit-log detection rules.
10. Finalize permission matrix before inviting users.

## Decision

Do both:

- Configure native Anypoint alerts for app/API behavior.
- Automate usage reporting through Anypoint Usage API for entitlement control.

Native alerts are not enough because they do not fully represent contractual burn. Usage API automation is the stronger control for the zero-dollar entitlement objective.
