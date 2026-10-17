"""Collect server-authored presentation text without loading archives or settings."""

import ast
from pathlib import Path

from localization_swift import natural_text


def template(node: ast.AST) -> str | None:
    counter = 0

    def part(value: ast.AST) -> str:
        nonlocal counter
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
        if isinstance(value, ast.JoinedStr):
            return "".join(
                part(item.value) if isinstance(item, ast.FormattedValue) else part(item)
                for item in value.values
            )
        if isinstance(value, ast.BinOp) and isinstance(value.op, ast.Add):
            return part(value.left) + part(value.right)
        name = "{value" + str(counter) + "}"
        counter += 1
        return name

    result = part(node)
    return result if natural_text(result) else None


def messages(root: Path) -> set[str]:
    result = set()
    backend = root / "src/omi_local"
    configuration = ast.parse((backend / "configuration.py").read_text())
    settings = ast.parse((backend / "settings.py").read_text())
    for node in configuration.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "DESCRIPTIONS" for t in node.targets
        ):
            result.update(ast.literal_eval(node.value).values())
        if isinstance(node, ast.FunctionDef) and node.name == "group":
            result.update(
                n.value
                for n in ast.walk(node)
                if isinstance(n, ast.Constant)
                and isinstance(n.value, str)
                and natural_text(n.value)
            )
    overrides = {}
    descriptor = next(
        n for n in configuration.body if isinstance(n, ast.FunctionDef) and n.name == "descriptors"
    )
    for node in ast.walk(descriptor):
        if isinstance(node, ast.Dict):
            try:
                data = ast.literal_eval(node)
                if "stt_model" in data:
                    overrides = data
            except ValueError, TypeError:
                pass
    model = next(n for n in settings.body if isinstance(n, ast.ClassDef) and n.name == "Settings")
    for field in model.body:
        if isinstance(field, ast.AnnAssign) and isinstance(field.target, ast.Name):
            name = field.target.id
            label = name.replace("_", " ").capitalize()
            for word in ("Stt", "Llm", "Api", "Omi"):
                label = label.replace(word, "Omi" if word == "Omi" else word.upper())
            result.add(overrides.get(name, label))
    for path in backend.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                path.name == "vendor_catalog.py"
                and isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "VendorDefinition"
            ):
                # Catalog labels and instructions are translated at each client’s
                # UI boundary. Keep their server-provided keys in both catalogs.
                for index in (1, 4):
                    if len(node.args) > index and isinstance(node.args[index], ast.Constant):
                        value = node.args[index].value
                        if isinstance(value, str) and value:
                            result.add(value)
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
                for arg in [
                    *node.exc.args,
                    *(
                        item.value
                        for item in node.exc.keywords
                        if item.arg in {"detail", "message"}
                    ),
                ]:
                    if isinstance(arg, (ast.Constant, ast.JoinedStr, ast.BinOp)) and (
                        value := template(arg)
                    ):
                        result.add(value)
            if path.name == "voice_matching.py" and isinstance(node, ast.Return):
                if isinstance(node.value, ast.Tuple) and len(node.value.elts) == 2:
                    reason = node.value.elts[1]
                    if isinstance(reason, (ast.Constant, ast.JoinedStr)) and (
                        value := template(reason)
                    ):
                        result.add(value)
            if isinstance(node, ast.Assign):
                presentation = {"message", "reason", "note", "boundary_note"}
                targets = node.targets
                value = node.value
                if (
                    len(targets) == 1
                    and isinstance(targets[0], ast.Tuple)
                    and isinstance(value, ast.Tuple)
                ):
                    pairs = zip(targets[0].elts, value.elts)
                else:
                    pairs = ((target, value) for target in targets)
                for target, value in pairs:
                    if isinstance(target, ast.Name) and target.id in presentation:
                        values = (
                            [value.body, value.orelse] if isinstance(value, ast.IfExp) else [value]
                        )
                        for item in values:
                            if isinstance(item, (ast.Constant, ast.JoinedStr, ast.BinOp)) and (
                                message := template(item)
                            ):
                                result.add(message)
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if isinstance(key, ast.Constant) and key.value in {
                        "notice",
                        "boundary_note",
                        "reason",
                        "message",
                    }:
                        if isinstance(value, (ast.Constant, ast.JoinedStr, ast.BinOp)) and (
                            message := template(value)
                        ):
                            result.add(message)
            if (
                path.name == "completion_events.py"
                and isinstance(node, ast.FunctionDef)
                and node.name in {"job_notice", "publish_recap", "publish_weekly"}
            ):
                for child in ast.walk(node):
                    if isinstance(child, (ast.Constant, ast.JoinedStr, ast.BinOp)) and (
                        message := template(child)
                    ):
                        result.add(message)
            if (
                path.name == "conversation_prep.py"
                and isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "LIMITATIONS" for t in node.targets)
            ):
                result.update(ast.literal_eval(node.value))
            if (
                path.name == "recaps.py"
                and isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "note" for t in node.targets)
            ):
                for child in ast.walk(node.value):
                    if (
                        isinstance(child, ast.Constant)
                        and isinstance(child.value, str)
                        and natural_text(child.value)
                    ):
                        result.add(child.value)
        if path.name == "recaps.py":
            base = next(
                ast.literal_eval(n.value)
                for n in tree.body
                if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "BOUNDARY_NOTE" for t in n.targets)
            )
            result.add(base)
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.AugAssign)
                    and isinstance(node.target, ast.Name)
                    and node.target.id == "note"
                ):
                    suffix = template(node.value)
                    if suffix:
                        result.add(base + suffix)
    return {text for text in result if text}
