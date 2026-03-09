"""Generate API reference pages for mkdocs-gen-files.

Walks the src/agent_kernel/ package and creates ::: directives
for mkdocstrings to render. Excludes internal modules like CLI,
MCP server, and runners.
"""

from pathlib import Path

import mkdocs_gen_files

nav = mkdocs_gen_files.Nav()

root = Path("src")
package = root / "agent_kernel"

# Modules to exclude from public API docs
EXCLUDE_MODULES = {
    "agent_kernel.mcp_server",
    "agent_kernel.cli",
    "agent_kernel.runners",
}


def _is_excluded(module_path: str) -> bool:
    """Check if a module path falls under an excluded namespace."""
    for excluded in EXCLUDE_MODULES:
        if module_path == excluded or module_path.startswith(excluded + "."):
            return True
    return False


for path in sorted(package.rglob("*.py")):
    module_path = path.relative_to(root)

    # Skip __main__.py files
    if path.name == "__main__.py":
        continue

    # Build the dotted module name
    if path.name == "__init__.py":
        # Package init -> use parent directory as module
        doc_path = path.relative_to(root).parent / "index.md"
        parts = list(path.relative_to(root).parent.parts)
    else:
        doc_path = path.relative_to(root).with_suffix(".md")
        parts = list(path.relative_to(root).with_suffix("").parts)

    # Convert path parts to dotted module name
    module_name = ".".join(parts)

    # Skip excluded modules
    if _is_excluded(module_name):
        continue

    # Skip empty module names
    if not module_name:
        continue

    # Create the reference page
    full_doc_path = Path("reference") / doc_path

    with mkdocs_gen_files.open(full_doc_path, "w") as fd:
        fd.write(f"::: {module_name}\n")

    mkdocs_gen_files.set_edit_path(full_doc_path, path)

    # Add to navigation
    nav_parts = list(doc_path.relative_to("agent_kernel").with_suffix("").parts)
    if nav_parts and nav_parts[-1] == "index":
        nav_parts = nav_parts[:-1]
    if nav_parts:
        nav[tuple(nav_parts)] = str(doc_path)

# Write the literate nav file for the reference section
with mkdocs_gen_files.open("reference/SUMMARY.md", "w") as nav_file:
    nav_file.writelines(nav.build_literate_nav())
