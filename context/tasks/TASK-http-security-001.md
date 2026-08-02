# Task packet: HTTP-SECURITY-001

Goal: harden the staff browser boundary with CSRF/origin enforcement, CSP and security headers,
trusted-host and request-size controls, authentication abuse limits, and real OIDC boundary tests.

No local password authority is introduced. Unknown identity, MFA, issuer, recovery, origin, or session
state fails closed and is safely audited.

