# Anypoint Platform API Surface — Inventory for Watchmen

Research date: 2026-05-15

Base URL: `https://anypoint.mulesoft.com`

All endpoints require `Authorization: Bearer {access_token}` header.
Most endpoints require `X-ANYPNT-ORG-ID` and `X-ANYPNT-ENV-ID` headers.

---

## 1. Access Management (accounts/api)

### Authentication

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/accounts/login` | Get access token (username/password) |
| GET | `/accounts/api/me` | Current user profile, org ID, org details |
| GET | `/accounts/api/profile` | User profile (name, email, phone, org) |

### Organizations & Environments

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/accounts/api/organizations/{orgId}` | Organization details |
| GET | `/accounts/api/organizations/{orgId}/environments` | List all environments (id, name, type, clientId) |

### Users & Roles

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/apiplatform/repository/v2/organizations/{orgId}/users` | List users in org |
| GET | `/accounts/api/organizations/{orgId}/members` | List org members |
| GET | `/accounts/api/organizations/{orgId}/rolegroups` | List role groups |

**Available fields (from /api/me):** org ID, org name, sub-org IDs, user ID, username, email, first/last name, roles, member of organizations.

**Available fields (environments):** id, name, organizationId, isProduction, type (sandbox/production/design), clientId.

**Watchmen use:** Enumerate environments to iterate API Manager and Runtime Manager per-environment. Know who has access.

---

## 2. CloudHub API (cloudhub/api) — CloudHub 1.0

### Applications

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/cloudhub/api/applications` | List all CloudHub apps in environment |
| GET | `/cloudhub/api/applications/{domain}` | Get app details |
| GET | `/cloudhub/api/applications/{domain}/schedules` | App polling schedules |

**Required headers:** `X-ANYPNT-ENV-ID`, `X-ANYPNT-ORG-ID`

**Response fields (list applications):**
- `domain` — app name/domain
- `fullDomain` — full URL
- `status` — STARTED, DEPLOYING, UNDEPLOYED, etc.
- `workers.type` — name, weight, cpu, memory
- `workers.amount` — number of workers
- `muleVersion.version` — Mule runtime version
- `fileName` — deployed artifact
- `lastUpdateTime` — timestamp
- `properties` — custom app properties
- `workerStatuses[]` — per-worker: id, host, port, status, deployedRegion

**Watchmen use:** Inventory of deployed CloudHub apps, worker sizing, status monitoring.

---

## 3. CloudHub 2.0 API (cloudhub-20)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v2/organizations/{orgId}/environments/{envId}/deployments` | List CH2 deployments |
| GET | `/v2/organizations/{orgId}/environments/{envId}/deployments/{deploymentId}` | Deployment details |

**Note:** CH2 is container-based. Response structure differs from CH1 — uses replicas instead of workers.

**Watchmen use:** If org uses CloudHub 2.0, needed alongside CH1 API.

---

## 4. Runtime Manager — On-Premises (hybrid/api)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/hybrid/api/v1/applications` | List on-prem deployed apps |
| GET | `/hybrid/api/v1/applications/{appId}` | App details |
| GET | `/hybrid/api/v1/servers` | List registered servers |
| GET | `/hybrid/api/v1/servers/{serverId}` | Server details |
| GET | `/hybrid/api/v1/serverGroups` | List server groups |
| GET | `/hybrid/api/v1/clusters` | List clusters |

**Required headers:** `X-ANYPNT-ENV-ID`, `X-ANYPNT-ORG-ID`

**Watchmen use:** Inventory of on-prem apps and infrastructure. Only relevant if org has hybrid/on-prem deployments.

---

## 5. API Manager (apimanager/api)

### API Instances

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/apimanager/api/v1/organizations/{orgId}/environments/{envId}/apis` | List API instances |
| GET | `/apimanager/api/v1/organizations/{orgId}/environments/{envId}/apis/{apiId}` | API instance detail |

**Response fields (list APIs):**
- `id` — API instance ID
- `instanceLabel` — human label
- `groupId`, `assetId`, `assetVersion` — Exchange asset reference
- `technology` — mule4, flexGateway, etc.
- `endpoint.uri` — implementation URI
- `endpoint.proxyUri` — proxy URI
- `endpoint.isCloudHub` — boolean
- `autodiscoveryInstanceName` — e.g. "v1:1234567"
- `environmentId`
- `organizationId`
- `tags`

### Environments (via xapi)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/apimanager/xapi/v1/organizations/{orgId}/environments` | List environments with API Manager context |

**Watchmen use:** Count managed API instances (maps to `api_manager_prod`/`api_manager_preprod` meters). Track which APIs exist, their technology, and deployment target.

---

## 6. Audit Log Query API (audit/v2)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/audit/v2/organizations/{orgId}/query` | Query audit events |

**Optional query params:** `cursorPagination=true`, `doIncludeTotal=capped`

**Request body:**
```json
{
  "startDate": "2026-05-14T00:00:00.000Z",
  "endDate": "2026-05-15T00:00:00.000Z",
  "platforms": ["CoreServices", "Runtime Manager", "api-platform-api"],
  "objectTypes": ["User", "Application", "policy"],
  "actions": ["Login", "Deploy", "edit"],
  "subactions": ["Enable user"],
  "environmentIds": ["env-id"],
  "objectIds": ["obj-id"],
  "userIds": ["user-id"],
  "organizationId": "org-id",
  "offset": 0,
  "limit": 50,
  "ascending": false
}
```

All request body fields are optional (empty body returns all recent events).

**Response fields (per event):**
- Time, Product, Type, Action, Object, User Name
- Connected App (if applicable)
- Environment (if applicable)
- Parent (optional)
- Payload (optional — action-specific details)

**Auditable platforms:** CoreServices, Runtime Manager, api-platform-api, Exchange, API Governance, Design Center, MQ, RPA, DataGraph

**Auditable object types:** User, Application, policy, Server, Cluster, ServerGroup, API, Contract, Portal, Organization, Environment, Role, ConnectedApp, Team, Project, Queue, Exchange, Binding, Client, and more.

**Auditable actions:** Login, Logout, Create, Delete, Deploy, Start, Stop, Edit, Approve, Reject, Grant, Revoke, Enable, Disable, and more.

**Retention:** 1 year default. 6 years for pre-July 2023 orgs.

**Watchmen use:** Detect platform changes — new deployments, user changes, role modifications, API creation/deletion, policy changes. The primary "what changed" signal.

---

## 7. Exchange API (exchange/api)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/exchange/api/v1/assets?organizationId={orgId}` | List Exchange assets |
| GET | `/exchange/api/v2/assets` | List assets (v2) |

**Watchmen use:** Track published assets, API specs, connectors. Lower priority for governance.

---

## 8. Design Center (designcenter/api)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/designcenter/api/v1/organizations/{orgId}/projects` | List Design Center projects |

**Response fields:** name, type, creation date, last update.

**Watchmen use:** Track API design activity. Lower priority.

---

## 9. Anypoint MQ (mq/admin/api)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/mq/admin/api/v1/organizations/{orgId}/environments/{envId}/regions` | List MQ regions |
| GET | `/mq/admin/api/v1/organizations/{orgId}/environments/{envId}/regions/{regionId}/destinations` | List queues/exchanges |

**Watchmen use:** Inventory MQ queues. Maps to `anypoint_mq_api_requests` meter.

---

## 10. Object Store (object-store-stats)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `https://object-store-stats.anypoint.mulesoft.com/api/v1/organizations/{orgId}/environments/{envId}` | Object Store stats |

**Watchmen use:** Maps to `object_store_effective_api_requests` meter.

---

## Summary: What's Available vs. What We Need

### For Inventory (who is running what)

| Need | API | Endpoint | Confidence |
|------|-----|----------|------------|
| CloudHub apps + workers | CloudHub API | GET /cloudhub/api/applications | HIGH — documented, tested |
| On-prem apps + servers | Runtime Manager | GET /hybrid/api/v1/applications | HIGH — documented |
| API Manager instances | API Manager | GET /apimanager/.../apis | HIGH — documented |
| Environments list | Access Mgmt | GET /accounts/.../environments | HIGH — documented |
| Users & roles | Access Mgmt | GET /.../{orgId}/users | HIGH — documented |
| MQ queues | MQ Admin | GET /mq/admin/.../destinations | HIGH — documented |

### For Change Detection (what changed and who did it)

| Need | API | Endpoint | Confidence |
|------|-----|----------|------------|
| All platform events | Audit Log | POST /audit/v2/.../query | HIGH — well documented |
| Filter by action type | Audit Log | objectTypes + actions params | HIGH |
| Filter by date range | Audit Log | startDate + endDate params | HIGH |
| Filter by user | Audit Log | userIds param | HIGH |
| Filter by environment | Audit Log | environmentIds param | HIGH |

### For Ownership Mapping (link usage to owners)

| Need | Source | Available? |
|------|--------|------------|
| Who deployed an app | Audit Log (Deploy action) | YES |
| App → environment mapping | CloudHub/Runtime Manager APIs | YES |
| API instance → asset mapping | API Manager API (assetId, groupId) | YES |
| User who created API instance | Audit Log (Create action on API) | YES |
| Purpose/customer/demo context | NOT IN ANY API — manual annotation | NO |

### Gaps (Not Available from APIs)

- **Business context:** No API tells you *why* something was deployed (demo, POC, production). This requires manual annotation (the Demo Registry concept).
- **Expected usage per workload:** No API provides usage expectations per app. Manual entry needed.
- **Expiry dates for demos:** Platform has no concept of "this should be torn down by date X". Manual tracking needed.
- **Per-app usage breakdown:** The Usage API gives aggregate meter totals, not per-application consumption. The dimensional data (consumer, environment) gives partial breakdown but not full attribution.

---

## Recommended Approach for Watchmen

**Phase A — Inventory Collection (automated):**
1. Enumerate environments via Access Management API
2. For each environment: pull CloudHub apps, API Manager instances
3. Store snapshots in SQLite
4. Diff against previous snapshot to detect additions/removals

**Phase B — Audit Log Integration (automated):**
1. Poll Audit Log API periodically (e.g., last 24h of events)
2. Store events in SQLite
3. Surface "recent changes" in UI with filters
4. Alert on risky changes (new deployments, role changes, connected apps)

**Phase C — Ownership Annotation (manual, UI-driven):**
1. Let admin annotate any inventory item with: owner, purpose, customer, expiry
2. Flag unregistered/unannotated workloads
3. Flag expired annotations still running

This order ensures each phase builds on real API data, and the manual registry (Phase C) enriches automated inventory rather than replacing it.
