import json
import re
from dataclasses import dataclass

from app.browser.refs import ELEMENT_REF


NODE = re.compile(r'''^\s*- ['"]?(?P<role>[a-z][a-z0-9-]*)\b''')
ATTRIBUTES = re.compile(r'\s*\[(?:ref=[^\]]+|level=\d+|cursor=pointer|active|checked|disabled|selected|expanded|pressed|aria-hidden)\]')
CONTROLS = {'button', 'link', 'textbox', 'searchbox', 'combobox', 'checkbox', 'radio', 'switch',
            'option', 'menuitem', 'menuitemradio', 'menuitemcheckbox', 'tab', 'slider', 'spinbutton'}


@dataclass
class Node:
    start: int
    end: int
    indent: int
    role: str
    text: str
    parent: object = None


def node_text(line, match):
    tail = line[match.end():].lstrip()
    if line.lstrip().startswith("- '"):
        tail = tail.replace("''", "'")
    if tail.startswith('"'):
        try:
            return json.JSONDecoder().raw_decode(tail)[0]
        except ValueError:
            pass
    tail = ATTRIBUTES.sub('', tail).lstrip(' :').removesuffix("'").strip()
    if tail.startswith('"'):
        try:
            return json.loads(tail)
        except ValueError:
            pass
    return tail


def nodes(lines):
    stack, result = [], []
    for index, line in enumerate(lines):
        match = NODE.match(line)
        if not match:
            continue
        indent = len(line) - len(line.lstrip())
        while stack and stack[-1].indent >= indent:
            stack.pop().end = index
        node = Node(index, len(lines), indent, match['role'], node_text(line, match), stack[-1] if stack else None)
        stack.append(node)
        result.append(node)
    return result


def compact_snapshot(text):
    """Remove empty wrappers and text already present in a control's accessible name."""
    lines = text.splitlines()
    tree = nodes(lines)
    skipped = set()
    for index, node in enumerate(tree):
        if node.start in skipped:
            continue
        if node.role == 'generic' and not node.text and '[cursor=pointer]' not in lines[node.start]:
            skipped.add(node.start)
        if node.role not in {'button', 'link', 'option'} or len(node.text) < 40 or node.end - node.start > 200:
            continue
        children = []
        for child in tree[index + 1:]:
            if child.start >= node.end:
                break
            children.append(child)
        label = ' '.join(node.text.split()).casefold()
        if children and all(child.role not in CONTROLS and (child.text in {'·', '•', '|'}
                            or ' '.join(child.text.split()).casefold() in label)
                            for child in children):
            skipped.update(child.start for child in children)
    return '\n'.join(line for index, line in enumerate(lines) if index not in skipped)


def matching_lines(lines, query):
    tree = nodes(lines)
    by_line = {node.start: node for node in tree}
    selected = set()
    for index, line in enumerate(lines):
        if query.casefold() not in line.casefold():
            continue
        node = by_line.get(index)
        container = node
        while container and container.role not in {'button', 'link', 'option', 'listitem', 'article', 'row', 'cell'}:
            container = container.parent
        if container and container.end - container.start <= 80:
            selected.update(range(container.start, container.end))
            parent = container.parent
            while parent:
                selected.add(parent.start)
                parent = parent.parent
        else:
            selected.update(range(max(0, index - 2), min(len(lines), index + 5)))
            parent = container or node
            while parent:
                selected.add(parent.start)
                parent = parent.parent
    return selected


def without_refs(text):
    return re.sub(rf'\s*\[ref={ELEMENT_REF}\]', '', text)
