SYSTEM_PROMPT = (
    "You are the Classification Agent in a CI pipeline. You are given the file "
    "tree and key manifest files of a source repository. Classify the "
    "repository's primary language/ecosystem so the CI Agent can pick the right "
    "build playbook. Only two ecosystems are supported: java (built with Maven) "
    "and dotnet (built with the dotnet CLI). Base your answer strictly on the "
    "provided facts and report honest confidence."
)


def render_facts(file_tree: list[str], manifests: dict[str, str]) -> str:
    lines = ["<file_tree>"]
    lines.extend(file_tree)
    lines.append("</file_tree>")
    for path, content in manifests.items():
        lines.append(f'<manifest path="{path}">')
        lines.append(content)
        lines.append("</manifest>")
    lines.append("Classify this repository.")
    return "\n".join(lines)
