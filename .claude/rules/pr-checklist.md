# Pull Request Checklist

## Before Creating PR

### Code Quality
- [ ] All tests pass locally (`make test`)
- [ ] Linting passes (`make lint`)
- [ ] Type checking passes (`make typecheck`)
- [ ] No debugging code or print statements remain
- [ ] Code follows existing formatting and naming conventions

### Testing
- [ ] Added tests for new functionality
- [ ] Tested edge cases and error handling
- [ ] Manual testing completed for new features

### Documentation
- [ ] Updated relevant documentation in `docs/design/`
- [ ] Added/updated docstrings for public APIs
- [ ] Updated `.claude/rules/` if patterns changed
- [ ] Commented complex logic where non-obvious

### Security
- [ ] No secrets or credentials committed
- [ ] Environment variables used for configuration
- [ ] Input validation implemented where needed

### PR Description
- [ ] Clear title summarizing the change
- [ ] **What**: Description of changes made
- [ ] **Why**: Reason/motivation for changes
- [ ] **How**: Technical approach used
- [ ] **Risks**: Potential issues or breaking changes
- [ ] **Follow-ups**: Future work or known limitations

## Small, Focused PRs

**Prefer:**
- Single concern or feature per PR
- Logical, atomic commits with clear messages
- Before/after examples where relevant

**Avoid:**
- Large PRs mixing multiple unrelated changes
- Refactoring mixed with feature work
- PRs that touch too many files/systems

## Keep CI Green

- Fix all CI failures before requesting review
- Monitor test results and address flaky tests
- Rebase/merge to resolve conflicts promptly
