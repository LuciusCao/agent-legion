from pathlib import Path

from scripts.architecture.import_dependencies import _dependencies, _module_name


def _strongly_connected_components(graph: dict[str, set[str]]) -> list[tuple[str, ...]]:
    index = 0
    indexes: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[tuple[str, ...]] = []

    def visit(module: str) -> None:
        nonlocal index
        indexes[module] = index
        lowlinks[module] = index
        index += 1
        stack.append(module)
        on_stack.add(module)

        for dependency in sorted(graph[module]):
            if dependency not in indexes:
                visit(dependency)
                lowlinks[module] = min(lowlinks[module], lowlinks[dependency])
            elif dependency in on_stack:
                lowlinks[module] = min(lowlinks[module], indexes[dependency])

        if lowlinks[module] != indexes[module]:
            return
        component: list[str] = []
        while stack:
            member = stack.pop()
            on_stack.remove(member)
            component.append(member)
            if member == module:
                break
        if len(component) > 1:
            components.append(tuple(sorted(component)))

    for module in sorted(graph):
        if module not in indexes:
            visit(module)
    return components


def _render_component(component: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted({name.removesuffix(".__init__") for name in component}))


def check_import_cycles(root: Path) -> list[str]:
    paths = sorted(root.glob("server/app/**/*.py"))
    modules = {_module_name(path.relative_to(root)): path for path in paths}
    known = set(modules)
    graph = {module: _dependencies(module, path, known) for module, path in sorted(modules.items())}
    rendered = sorted(
        component
        for component in (
            _render_component(component) for component in _strongly_connected_components(graph)
        )
        if len(component) > 1
    )
    return [f"import cycle: {' -> '.join(component)}" for component in rendered]
