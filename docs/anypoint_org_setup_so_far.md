# Anypoint Platform Presales Setup So Far

## Context and intent
This Anypoint Platform organization is being configured for presales use only: demos, proofs of concept, testing of product capabilities, and presentations.[cite:7][cite:10] It is not intended for production integrations, internal production processes, or customer production workloads.[cite:7][cite:10]

The operating model therefore prioritizes low complexity, tight administrative control, and strong protection against accidental usage that could push the tenant beyond the free tier into paid consumption.[cite:7][cite:8][cite:10][cite:13]

## 1. Define business group model
The current recommended structure is intentionally minimal: keep the root organization for top-level administration and create a single presales child business group named `Example-Org`.[cite:7][cite:10] This structure isolates day-to-day presales work from root governance without overengineering the organization too early.[cite:7][cite:10]

Current status:
- Root organization exists and is being used for top-level administration.[cite:17]
- Presales child business group `Example-Org` has been proposed as the main working container for demos and PoCs.[cite:7][cite:10]
- No broader multi-BG hierarchy has been recommended at this stage because the current usage is limited to presales objectives.[cite:7][cite:10]

## 2. Establish environment strategy
The recommended environment model inside `Example-Org` is minimal and purpose-driven rather than a full enterprise SDLC design.[cite:10] The proposed initial environments are `demo-nonprod`, `demo-prod-like`, and optionally `training` if internal enablement sessions need a separated space.[cite:10]

Current status:
- Environment naming strategy has been defined conceptually.[cite:10]
- The naming intentionally signals demo usage rather than real production use.[cite:10]
- Detailed environment-level permissions and limits have not yet been finalized.[cite:8][cite:13]

## 3. Decide identity and SSO model
The current recommended direction is to start simple and avoid overbuilding identity integration before the tenant grows.[cite:2][cite:8] Access should be granted primarily through teams, with native login and tightly restricted admin membership initially, then enterprise IdP integration can be added later if onboarding volume justifies it.[cite:2][cite:8][cite:13]

Current status:
- No full IdP/SSO implementation has been confirmed yet.[cite:18]
- The platform is currently being managed directly by the two root users.[cite:17]
- Identity-provider integration remains a future option rather than an immediate prerequisite for presales launch.[cite:2][cite:8]

## 4. Design teams and role model
The agreed team model uses one small root admin team plus several business-group-level teams for constrained access.[cite:8][cite:13] The root-level admin team already exists under the name `Root-Admins`, and it is being treated as the functional equivalent of the proposed `Org-Platform-Admins` team.[cite:17][cite:8][cite:13]

Current status:
- `Root-Admins` exists and has been validated as the correct top-level admin team pattern for this tenant.[cite:17]
- `Org-Leads` exists as a business-group-level team and its Limits page is currently open, confirming the team has already been created.[cite:18]
- `Org-Users` and `Org-Observers` remain part of the agreed target model but have not yet been confirmed in the current working session.[cite:8][cite:13]

Operational guidance agreed so far:
- `Root-Admins` should contain only two named admins, both as maintainers.[cite:17][cite:8][cite:13]
- `Root-Admins` should remain a pure admin team with no child teams and no broader inheritance path to larger user populations.[cite:8][cite:13]
- Most future users should be placed in non-admin teams only, with restrictions implemented there rather than on root admins.[cite:8][cite:13]

## 5. Map permissions to personas
The target operating model separates users into clear personas such as root admins, presales leads, standard presales users, and read-only observers.[cite:8][cite:13] This structure is intended to keep administrative power concentrated in a very small group while allowing controlled experimentation for presales users.[cite:8][cite:13]

Current status:
- Root admin persona is defined and mapped to `Root-Admins`.[cite:17]
- High-level persona model has been defined for `Org-Leads`, `Org-Users`, and `Org-Observers`.[cite:8][cite:13]
- Exact permission assignments per persona are still pending and should be finalized before broad user invitation begins.[cite:8][cite:13]

## 6. Plan asset sharing and governance
The long-term model anticipates controlled use of Exchange, API Manager, Runtime Manager, gateways, and other Anypoint capabilities for demos and PoCs, but under presales-oriented governance rather than enterprise production governance.[cite:4][cite:8][cite:13] The current posture is to allow experimentation while preventing uncontrolled publishing, deployment, or public exposure of assets that could increase cost or operational risk.[cite:4][cite:8][cite:13]

Current status:
- Governance intent is defined at a policy level: controlled experimentation, no open self-service, and careful access segmentation.[cite:8][cite:13]
- No detailed Exchange sharing model, asset visibility model, or API governance ruleset rollout has been finalized yet.[cite:4][cite:8]
- This remains a pending design area to complete after the team and permission model is locked down.[cite:4][cite:8][cite:13]

## 7. Security baselines and external access
The security direction so far is pragmatic: keep root administration tightly restricted, avoid broad administrative permissions, and limit exposure points that could create cost or security issues in a demo tenant.[cite:2][cite:7][cite:13] The currently visible Access Management area includes Identity Providers, Client Providers, Audit Logs, Security Settings, External Access, Trusted Domains, and subscription sections for Runtime Manager, MQ, and Object Store, which confirms the administrative domains that root-level governance will eventually need to control.[cite:18]

Current status:
- Root-level control domains are visible and available in Access Management.[cite:18]
- No detailed external access posture, connected-app strategy, trusted-domain model, or customer-facing exposure model has been finalized yet.[cite:18]
- Security baseline design is still pending as a structured work item after teams and permissions are completed.[cite:2][cite:7][cite:13]

## 8. Invitation and onboarding flow
The agreed onboarding principle is that broad invitations should wait until the business group, teams, permissions, and guardrails are properly defined.[cite:7][cite:8][cite:13] For this tenant, onboarding should be conservative because the free-tier cost model makes uncontrolled experimentation expensive if it crosses platform limits.[cite:7][cite:8][cite:10][cite:13]

Current status:
- The tenant is currently operated by two root users only.[cite:17]
- Broader user invitation has not yet started in the documented setup sequence.[cite:17][cite:18]
- Invitation and onboarding workflow design remains pending until non-admin teams and permission sets are finalized.[cite:8][cite:13]

## Core decisions captured so far
The following decisions or working assumptions are now part of the setup baseline:

- This tenant is presales-only and not for production use.[cite:7][cite:10]
- Cost control and prevention of accidental paid-tier consumption are primary design drivers.[cite:7][cite:8][cite:10][cite:13]
- Root administration should stay limited to two users through `Root-Admins`.[cite:17][cite:8][cite:13]
- A single child BG `Example-Org` is the preferred starting structure.[cite:7][cite:10]
- Team-based access control is preferred over ad hoc individual user grants.[cite:8][cite:13]
- Most design work beyond root administration is still intentionally being completed before broad user invitations.[cite:8][cite:13]

## Items 4–8 — Status
| Item | Status | Notes |
|------|--------|-------|
| 4. Design teams and role model | In progress | `Root-Admins` has been validated; `Org-Leads` exists; remaining BG teams and detailed role boundaries still need to be completed.[cite:17][cite:18][cite:8][cite:13] |
| 5. Map permissions to personas | Pending | Persona model is defined conceptually, but exact permissions per team have not yet been finalized.[cite:8][cite:13] |
| 6. Plan asset sharing and governance | Pending | Governance intent is clear, but Exchange sharing, asset visibility, and API governance controls are still to be designed.[cite:4][cite:8][cite:13] |
| 7. Security baselines and external access | Pending | Administrative security domains are visible in Access Management, but baseline settings and exposure model are not yet defined.[cite:18][cite:2][cite:7] |
| 8. Invitation and onboarding flow | Pending | Broader invitations should wait until teams, permissions, and controls are finalized.[cite:7][cite:8][cite:13] |

## Suggested next working step
The next working step should be to finish item 4 in a hands-on way: define the exact purpose, permissions, and limits of `Org-Leads`, then repeat the same exercise for `Org-Users` and `Org-Observers`.[cite:18][cite:8][cite:13]
