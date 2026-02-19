# Version Tracking Workflow

## Overview

This project now uses semantic versioning to track changes and deployments.

**Current Version:** `1.1.0`

## Version Format

We use [Semantic Versioning](https://semver.org/): `MAJOR.MINOR.PATCH`

- **MAJOR** - Breaking changes (e.g., 2.0.0)
- **MINOR** - New features, backward compatible (e.g., 1.2.0)
- **PATCH** - Bug fixes, backward compatible (e.g., 1.1.1)

## Workflow

### 1. Make Changes
Edit code, fix bugs, add features

### 2. Test & Verify
Confirm the feature/fix works

### 3. Bump Version
```bash
./bump_version.sh
```

Choose version type:
- Patch (bug fixes) → 1.1.1
- Minor (new features) → 1.2.0
- Major (breaking changes) → 2.0.0
- Custom → Enter manually

### 4. Review CHANGELOG
The script automatically updates:
- `VERSION` file
- `custom_components/ha_dispatch_client/manifest.json`
- `CHANGELOG.md`

Review the CHANGELOG entry and edit if needed.

### 5. Deploy
```bash
./deploy.sh
```

The deploy script will show the version being deployed!

## Files Involved

### VERSION
```
1.1.0
```
Single line file with current version.

### manifest.json
```json
{
  "domain": "ha_dispatch_client",
  "name": "HA Dispatch Client",
  "version": "1.1.0",
  ...
}
```

### CHANGELOG.md
Documents all changes per version.

## Version History

### v1.1.0 (2025-11-16) ✅ Current
**Fixed:**
- Service registration issue (services now appear in HA)

**Added:**
- Unified deploy script
- Version tracking
- Automatic backups

### v1.0.0 (2025-11-15)
**Initial Release:**
- Client registration
- Metrics collection
- 4 debug services
- Sensor entities

## Quick Reference

```bash
# Check current version
cat VERSION

# Bump version (interactive)
./bump_version.sh

# Deploy current version
./deploy.sh

# View version history
cat CHANGELOG.md
```

## When to Bump Versions

### Patch (1.1.X)
- Bug fixes
- Documentation updates
- Typo corrections
- Performance improvements

### Minor (1.X.0)
- New features
- New services
- New sensors
- Enhanced functionality
- Backward compatible changes

### Major (X.0.0)
- Breaking API changes
- Removed features
- Major refactoring
- Incompatible updates

## Example Workflow

```bash
# 1. Fix a bug in coordinator.py
nano custom_components/ha_dispatch_client/coordinator.py

# 2. Test it works
./deploy.sh
# Verify fix on HA

# 3. Bump version (patch)
./bump_version.sh
# Select: 1 (Patch)
# Enter: "Fixed uptime calculation bug"

# 4. Deploy new version
./deploy.sh
# Shows: Version: 1.1.1

# 5. Commit changes
git add VERSION CHANGELOG.md custom_components/
git commit -m "v1.1.1: Fixed uptime calculation bug"
```

## Benefits

✅ **Track Progress** - Know exactly what version is deployed  
✅ **Change History** - CHANGELOG documents all changes  
✅ **Easy Rollback** - Can identify versions to restore from backups  
✅ **Professional** - Follows standard versioning practices  
✅ **Deployment Clarity** - deploy.sh shows version being installed  

## Integration with Deploy Script

The `deploy.sh` script reads the VERSION file and displays it:

```
╔════════════════════════════════════════════╗
║    HA Dispatch Client - Deploy Script     ║
║           Version: 1.1.0                   ║
╚════════════════════════════════════════════╝
```

After deployment:
```
Updated Integration: HA Dispatch Client v1.1.0
```

This makes it crystal clear what version is being deployed!

## Memory Integration

The AI assistant has been instructed to:
1. ✅ Track version after confirming features work
2. ✅ Update VERSION file when appropriate
3. ✅ Update CHANGELOG.md with changes
4. ✅ Prompt you to run `./deploy.sh` after version bumps

This ensures you always know:
- What version is in development
- What version is deployed
- What changed between versions

