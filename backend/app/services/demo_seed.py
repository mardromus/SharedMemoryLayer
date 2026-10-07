"""Seeded demonstration environment.

Everything is created through the real service layer (so every memory is
embedded, stored in Qdrant, versioned and audited exactly like production
traffic). The seed runs once: if the Development team already exists it is
skipped.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.enums import MemoryType, RoleName, Sensitivity, VerificationStatus, Visibility
from app.models.identity import Agent, Role, Team, User
from app.models.memory import Project
from app.schemas.identity import AgentCreate, ProjectCreate, TeamCreate
from app.schemas.memory import FeedbackCreate, MemoryCreate, MemoryUpdate, ShareRequest, VerifyRequest
from app.security.access import memory_payload
from app.security.passwords import hash_password
from app.security.principal import Principal
from app.services.identity_service import IdentityService
from app.services.memory_service import MemoryService

logger = logging.getLogger(__name__)

TEAMS = [
    ("Development", "Builds and maintains the product's services and front-ends."),
    ("QA", "Owns test strategy, automation and release quality."),
    ("Security", "Application security, vulnerability management and security reviews."),
    ("Support", "Customer support: incidents, FAQs and escalations."),
]

AGENTS = [
    ("Dev-Agent", "Development", "development", ["coding", "architecture", "technical-decisions"],
     "You write and review code, make architecture decisions and debug technical problems."),
    ("QA-Agent", "QA", "testing", ["test-design", "bug-triage", "qa-decisions"],
     "You design test cases, triage bugs and decide on release readiness."),
    ("Security-Agent", "Security", "security", ["vulnerability-analysis", "threat-modeling", "security-review"],
     "You find vulnerabilities, assess risk and give security recommendations."),
    ("Support-Agent", "Support", "support", ["customer-issues", "faq", "escalation"],
     "You resolve customer issues and maintain support knowledge."),
]

HUMANS = [
    ("dev.lead", "Dana Developer", RoleName.TEAM_MANAGER, "Development"),
    ("qa.lead", "Quinn Tester", RoleName.TEAM_MANAGER, "QA"),
    ("sec.lead", "Sam Security", RoleName.TEAM_MANAGER, "Security"),
    ("support.lead", "Riley Support", RoleName.TEAM_MANAGER, "Support"),
    ("auditor", "Avery Auditor", RoleName.READ_ONLY, None),
]

PROJECTS = [
    ("Customer Portal", "Web portal where customers manage accounts and invoices.", "Development"),
    ("Payments API", "Payment processing and webhook delivery service.", "Development"),
    ("Mobile App", "iOS and Android customer application.", "Development"),
]

# key, agent, title, content, type, visibility, sensitivity, tags, project, importance, confidence, shared_with_teams
MEMORIES = [
    ("jwt", "Dev-Agent", "JWT authentication fails when refresh tokens expire",
     "JWT authentication fails when refresh tokens expire. The /auth/refresh endpoint raised an unhandled "
     "ExpiredSignatureError and returned HTTP 500, which logged users out of the Customer Portal. Fix: catch the "
     "expiry, return 401 with error code token_expired and let the client re-authenticate.",
     MemoryType.LEARNING, Visibility.TEAM, Sensitivity.INTERNAL, ["authentication", "jwt", "refresh-token"],
     "Customer Portal", 0.85, 0.92, ["QA"]),
    ("db", "Dev-Agent", "Primary database selection for Customer Portal",
     "MongoDB was selected as the primary database for the Customer Portal because the early data model was schemaless.",
     MemoryType.DECISION, Visibility.ORGANIZATION, Sensitivity.INTERNAL, ["database", "architecture"],
     "Customer Portal", 0.9, 0.7, []),
    ("retry", "Dev-Agent", "Payment webhook retry policy",
     "Retry failed payment webhook deliveries with exponential backoff (1s, 2s, 4s, 8s, 16s; max 5 attempts). "
     "Every delivery carries an idempotency key so duplicate deliveries never double-charge a customer.",
     MemoryType.SOLUTION, Visibility.ORGANIZATION, Sensitivity.INTERNAL, ["payments", "webhooks", "reliability"],
     "Payments API", 0.8, 0.9, []),
    ("deploy", "Dev-Agent", "Deployment service-account rotation procedure",
     "Deployment service accounts are rotated every 30 days through the internal vault. The break-glass procedure "
     "and the list of privileged pipelines live in the private Development runbook.",
     MemoryType.CONTEXT, Visibility.TEAM, Sensitivity.CONFIDENTIAL, ["deployment", "credentials", "confidential"],
     None, 0.7, 0.95, []),
    ("n1", "Dev-Agent", "N+1 query problem in order history API",
     "The order history endpoint issued one SQL query per order line (N+1). Using a joined eager load reduced "
     "p95 latency from 1.8 s to 120 ms for customers with 200+ orders.",
     MemoryType.EXPERIENCE, Visibility.ORGANIZATION, Sensitivity.INTERNAL, ["performance", "database", "orm"],
     "Customer Portal", 0.6, 0.85, []),
    ("token-life", "Dev-Agent", "Access token lifetime configuration",
     "Access token lifetime is 15 minutes. Refresh tokens live for 7 days and are rotated on every use.",
     MemoryType.KNOWLEDGE, Visibility.ORGANIZATION, Sensitivity.INTERNAL, ["authentication", "configuration", "jwt"],
     "Customer Portal", 0.7, 0.9, []),
    ("qa-auth", "QA-Agent", "Authentication tests should include expired refresh tokens",
     "Authentication test suites must include cases with expired, revoked and malformed refresh tokens. The "
     "production outage caused by expired refresh tokens was not caught because only happy-path logins were tested.",
     MemoryType.LEARNING, Visibility.ORGANIZATION, Sensitivity.INTERNAL, ["authentication", "testing", "refresh-token"],
     "Customer Portal", 0.8, 0.9, []),
    ("flaky", "QA-Agent", "Flaky checkout end-to-end test root cause",
     "The checkout E2E test failed intermittently because it asserted on the order total before the async tax "
     "calculation finished. Waiting for the 'tax-calculated' event instead of a fixed sleep removed the flakiness.",
     MemoryType.INCIDENT, Visibility.TEAM, Sensitivity.INTERNAL, ["e2e", "flaky-tests", "checkout"],
     "Customer Portal", 0.6, 0.85, ["Development"]),
    ("regression", "QA-Agent", "Regression suite runtime and smoke strategy",
     "The full regression suite takes 42 minutes. Run the 6-minute smoke suite on every pull request and the full "
     "suite nightly and before each release.",
     MemoryType.KNOWLEDGE, Visibility.TEAM, Sensitivity.INTERNAL, ["regression", "ci", "testing"], None, 0.5, 0.9, []),
    ("sec-401", "Security-Agent", "Expired token handling must return appropriate HTTP status",
     "APIs must answer expired or invalid access/refresh tokens with 401 Unauthorized and a WWW-Authenticate header. "
     "Returning 500 leaks stack traces and returning 200 with an error body breaks client retry logic.",
     MemoryType.KNOWLEDGE, Visibility.ORGANIZATION, Sensitivity.INTERNAL, ["authentication", "http", "security-standard"],
     None, 0.85, 0.95, []),
    ("ssrf", "Security-Agent", "Unpatched SSRF vulnerability in PDF export service",
     "CONFIDENTIAL: the PDF export service fetches user-supplied image URLs without an allow-list, enabling SSRF "
     "against internal metadata endpoints. Fix is scheduled; do not disclose outside the Security team.",
     MemoryType.INCIDENT, Visibility.TEAM, Sensitivity.CONFIDENTIAL, ["ssrf", "vulnerability", "pdf-export", "confidential"],
     "Customer Portal", 0.95, 0.9, []),
    ("hashing", "Security-Agent", "Password hashing standard",
     "Argon2id is the approved password hashing algorithm for all services. bcrypt with cost 12 or higher is "
     "acceptable only for legacy systems during migration. Never use MD5 or SHA-1 for passwords.",
     MemoryType.DECISION, Visibility.ORGANIZATION, Sensitivity.INTERNAL, ["passwords", "cryptography", "security-standard"],
     None, 0.9, 0.95, []),
    ("deps", "Security-Agent", "Q3 dependency vulnerability scan findings",
     "The Q3 dependency scan found 3 high-severity issues: an outdated XML parser (XXE), a vulnerable image library "
     "and an old JWT library accepting the 'none' algorithm. Development must upgrade all three before release.",
     MemoryType.KNOWLEDGE, Visibility.SELECTED_TEAMS, Sensitivity.INTERNAL, ["dependencies", "vulnerability", "jwt"],
     None, 0.8, 0.9, ["Development"]),
    ("cookies", "Security-Agent", "Session cookie hardening",
     "Session and refresh-token cookies must be HttpOnly, Secure and SameSite=Strict, scoped to the narrowest path.",
     MemoryType.SOLUTION, Visibility.ORGANIZATION, Sensitivity.INTERNAL, ["cookies", "session", "security-standard"],
     None, 0.7, 0.9, []),
    ("logout", "Support-Agent", "Customers logged out unexpectedly after 15 minutes",
     "Several customers reported being logged out of the portal roughly 15 minutes after signing in, losing unsaved "
     "form data. Engineering linked it to the refresh-token expiry bug; advise customers to sign in again until the fix ships.",
     MemoryType.INCIDENT, Visibility.ORGANIZATION, Sensitivity.INTERNAL, ["customer-issue", "authentication", "logout"],
     "Customer Portal", 0.7, 0.8, []),
    ("reset", "Support-Agent", "Password reset emails delayed",
     "Password reset emails were delayed by up to 20 minutes because the transactional email queue was shared with "
     "marketing campaigns. A dedicated high-priority queue resolved it.",
     MemoryType.EXPERIENCE, Visibility.TEAM, Sensitivity.INTERNAL, ["email", "password-reset"], None, 0.5, 0.85, []),
    ("pdf-faq", "Support-Agent", "FAQ: exporting invoices as PDF",
     "Customers can export invoices as PDF from Billing > Invoices > Export. Bulk export is limited to 100 invoices per request.",
     MemoryType.DOCUMENTATION, Visibility.ORGANIZATION, Sensitivity.PUBLIC, ["faq", "invoices", "pdf-export"],
     "Customer Portal", 0.4, 0.95, []),
    ("escalation", "Support-Agent", "Personal escalation checklist",
     "My own checklist before escalating a ticket: reproduce, collect request ids, check status page, check known incidents.",
     MemoryType.CONTEXT, Visibility.PRIVATE, Sensitivity.INTERNAL, ["escalation"], None, 0.3, 0.9, []),
]


def _admin_principal(db: Session) -> Principal | None:
    admin = db.scalar(select(User).where(User.username == get_settings().admin_username))
    return Principal.from_user(admin) if admin else None


def seed_demo(db: Session) -> bool:
    if db.scalar(select(Team.id).where(Team.name == "Development")):
        return False
    admin = _admin_principal(db)
    if admin is None:
        logger.warning("demo seed skipped: administrator account missing")
        return False
    logger.info("seeding demonstration environment")
    identity = IdentityService(db)
    memories = MemoryService(db)

    teams = {name: identity.create_team(admin, TeamCreate(name=name, description=desc)) for name, desc in TEAMS}
    for name, desc, team in PROJECTS:
        identity.create_project(admin, ProjectCreate(name=name, description=desc, team_id=teams[team].id))
    projects = {p.name: p for p in db.scalars(select(Project))}

    agents: dict[str, Agent] = {}
    for name, team, agent_type, caps, persona in AGENTS:
        agent, _ = identity.create_agent(
            admin,
            AgentCreate(name=name, team_id=teams[team].id, agent_type=agent_type, capabilities=caps, persona=persona,
                        description=f"Demo {agent_type} agent", issue_api_key=False),
        )
        agents[name] = agent

    password = get_settings().demo_user_password
    if password:
        for username, full_name, role_name, team in HUMANS:
            role = db.scalar(select(Role).where(Role.name == role_name))
            db.add(User(username=username, full_name=full_name, email=f"{username}@example.com", password_hash=hash_password(password),
                        role_id=role.id, team_id=teams[team].id if team else None))
        db.commit()
    else:
        logger.warning("DEMO_USER_PASSWORD not set - demo human users were not created")

    created = {}
    for key, agent_name, title, content, mtype, vis, sens, tags, project, imp, conf, share in MEMORIES:
        principal = Principal.from_agent(agents[agent_name])
        memory, _ = memories.create(
            principal,
            MemoryCreate(
                title=title, content=content, memory_type=mtype, visibility=vis, sensitivity=sens, tags=tags,
                project_id=projects[project].id if project else None, importance=imp, confidence=conf,
                source="demo-seed", source_ref=f"seed:{key}",
            ),
        )
        if share:
            memories.share(principal, memory.id, ShareRequest(team_ids=[teams[t].id for t in share]))
        created[key] = memory

    dev = Principal.from_agent(agents["Dev-Agent"])
    # Version history example: MongoDB -> PostgreSQL -> PostgreSQL + Redis
    memories.update(dev, created["db"].id, MemoryUpdate(
        content="PostgreSQL was selected as the primary database for the Customer Portal because invoices and accounts need relational integrity and transactions.",
        confidence=0.85, change_description="Switched from MongoDB to PostgreSQL after data-model review"))
    memories.update(dev, created["db"].id, MemoryUpdate(
        content="PostgreSQL + Redis was selected: PostgreSQL remains the primary database for the Customer Portal and Redis is added as a cache and session store.",
        confidence=0.9, change_description="Added Redis as cache/session store"))

    # A conflicting statement from another team -> flagged for review
    qa = Principal.from_agent(agents["QA-Agent"])
    memories.create(qa, MemoryCreate(
        title="Token lifetime observed in staging",
        content="Access token lifetime is 30 minutes in the staging environment according to the auth service config.",
        memory_type=MemoryType.KNOWLEDGE, visibility=Visibility.ORGANIZATION, tags=["authentication", "configuration", "staging"],
        source="demo-seed", source_ref="seed:token-conflict", confidence=0.6,
    ))

    # An expired memory (hidden from standard retrieval)
    support = Principal.from_agent(agents["Support-Agent"])
    expired, _ = memories.create(support, MemoryCreate(
        title="Temporary workaround: disable 2FA reminder banner",
        content="During the September notification outage, advise customers to dismiss the 2FA reminder banner; it will reappear after the fix.",
        memory_type=MemoryType.SOLUTION, visibility=Visibility.ORGANIZATION, tags=["workaround", "2fa"],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1), source="demo-seed", source_ref="seed:expired",
    ))
    expired.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    db.commit()
    memories.store.set_payload(str(expired.id), memory_payload(expired))

    # Quality signals
    memories.feedback(qa, created["jwt"].id, FeedbackCreate(rating=5, helpful=True, comment="Reproduced it in the auth test suite."))
    memories.verify(admin, created["hashing"].id, VerifyRequest(status=VerificationStatus.VERIFIED, note="Matches the security policy"))
    memories.verify(admin, created["sec-401"].id, VerifyRequest(status=VerificationStatus.VERIFIED, note="Approved standard"))
    logger.info("demo environment seeded: %s memories", len(created) + 2)
    return True
