# Documentation Update Process

## Run Count

Total documentation update runs: **5**

| Run | Date       | Scope              | Files Changed | Notes                              |
|-----|------------|--------------------|---------------|------------------------------------|
| 1   | 2026-03-18 | Full restructure   | ~40 files     | Initial docs/ setup                |
| 2   | 2026-09-17 | Self-update spec   | 5 files       | New design spec + index/roadmap    |
| 3   | 2026-09-17 | Self-update v1.5.0 | 9 files       | Shipped client; new user guide     |
| 4   | 2026-09-18 | Server side built  | 1 file        | self-update.md status change       |
| 5   | 2026-09-19 | Remote access v1.6.0 | 9 files     | New technical doc; API endpoints 7-11 |

---

## When to Update Documentation

- After adding or changing a feature
- After adding or changing a service
- After modifying API endpoints or data structures
- After fixing a bug that changes user-facing behavior
- After changing deployment or configuration procedures
- Periodically to ensure accuracy (quarterly recommended)

## Update Workflow

### 1. Identify What Changed
- Review recent commits and CHANGELOG.md
- Note any new features, services, config options, or bug fixes

### 2. Update Affected Docs
- **User-facing changes** → update files in `docs/user/`
- **API or code changes** → update files in `docs/technical/`
- **Feature additions** → update `docs/leadership/capabilities.md` and `roadmap.md`
- **All changes** → check if `docs/INDEX.md` needs new entries

### 3. Record the Update
- Add dated entry to `docs/DOCS_CHANGELOG.md`
- Increment the run count in this file
- Add a new row to the run count table

### 4. Verify
- Check all internal links resolve (search for broken `](` references)
- Ensure `docs/INDEX.md` links to every doc file
- Confirm no stale references to deleted files

## Documentation Standards

### File Naming
- Lowercase with hyphens: `alert-api.md`, `services-guide.md`
- README.md for section TOCs (PascalCase exception)

### Content Guidelines
- **User docs**: Step-by-step, no code internals, focus on "how to"
- **Technical docs**: Code references, schemas, architecture decisions
- **Leadership docs**: No code blocks, business language, feature-focused
- **Archive docs**: Historical header, preserved as-is

### Cross-Linking
- Use relative paths: `[Services](../technical/services.md)`
- Link from INDEX.md to every new file
- Update section README.md files when adding docs

### Quality Checklist
- [ ] All internal links resolve
- [ ] No references to deleted root .md files
- [ ] INDEX.md is up to date
- [ ] DOCS_CHANGELOG.md has dated entry
- [ ] Run count incremented in this file
- [ ] CLAUDE.md documentation section is current

## File Structure Reference

```
docs/
├── INDEX.md                    # Master TOC
├── DOCS_CHANGELOG.md           # Doc-specific changelog
├── technical/                  # Developer audience
│   ├── README.md
│   ├── architecture.md
│   ├── api-reference.md
│   ├── services.md
│   ├── service-registration.md
│   ├── alert-api.md
│   ├── self-update.md
│   ├── remote-access.md
│   ├── data-model/
│   │   ├── README.md
│   │   ├── schema.md
│   │   ├── relationships.md
│   │   ├── api-endpoints.md
│   │   ├── json-examples.md
│   │   └── business-rules.md
│   └── dev-guide/
│       ├── README.md
│       └── (12 topic files)
├── user/                       # End-user audience
│   ├── README.md
│   ├── quickstart.md
│   ├── installation.md
│   ├── configuration.md
│   ├── services-guide.md
│   ├── alerts-guide.md
│   ├── deployment.md
│   ├── testing.md
│   └── troubleshooting.md
├── leadership/                 # Executive audience
│   ├── README.md
│   ├── executive-summary.md
│   ├── capabilities.md
│   ├── roadmap.md
│   └── technical-profile.md
└── archive/                    # Historical docs
    ├── README.md
    └── (7 archived files)
```
