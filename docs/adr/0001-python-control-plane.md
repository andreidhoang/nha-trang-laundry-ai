# ADR-0001: Python control plane and evaluation workspace

**Status:** accepted  
**Date:** 2026-07-27

## Context

The original roadmap proposed a TypeScript monolith. The product owner has chosen a Python-first
backend to prioritize applied AI/ML experimentation, agent evaluation, data analysis, and a single
language for the API, worker, policy/domain code, tool facade, and evaluation harness.

The system remains a deterministic operations product. Python does not grant an LLM authority over
money, policy, state, or side effects.

## Decision

- Use Python 3.12 as the supported baseline, managed through `uv`.
- Use a `uv` workspace with a universal lockfile and package boundaries for API, worker, domain,
  contracts, policy, observability, and evals.
- Use FastAPI for the HTTP boundary and Pydantic for typed boundary validation.
- Retain TypeScript only for the future React/Vite staff PWA, where browser ergonomics justify it.
- Keep business contracts language-neutral: OpenAPI/JSON Schema/JSON/YAML are authoritative.

## Consequences

- Python service and evaluation work share types, fixtures, and operational conventions.
- The web PWA consumes versioned API contracts; it may not duplicate business rules.
- Any later Python ML component must remain behind typed interfaces and pass the same release/eval
  gates as other agent capabilities.

