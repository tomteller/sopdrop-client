#!/usr/bin/env python
"""Fix scaling in CollectionListWidget class."""

import re

def fix_scaling(file_path):
    with open(file_path, 'r') as f:
        content = f.read()

    # Find the CollectionListWidget class
    class_match = re.search(r'class CollectionListWidget\(QtWidgets\.QWidget\):(.*?)(?=\nclass |\Z)', content, re.DOTALL)
    if not class_match:
        print("CollectionListWidget class not found")
        return

    class_content = class_match.group(0)
    class_start = class_match.start()

    # Make replacements in the class content
    fixed = class_content

    # 1. setContentsMargins(8, 12, 8, 12)
    fixed = fixed.replace(
        'container_layout.setContentsMargins(8, 12, 8, 12)',
        'container_layout.setContentsMargins(scale(8), scale(12), scale(8), scale(12))'
    )

    # 2. setSpacing(4)
    fixed = fixed.replace(
        'container_layout.setSpacing(4)',
        'container_layout.setSpacing(scale(4))'
    )

    # 3. sep_container.setFixedHeight(16)
    fixed = fixed.replace(
        'sep_container.setFixedHeight(16)',
        'sep_container.setFixedHeight(scale(16))'
    )

    # 4. sep_layout.setContentsMargins(8, 0, 8, 0)
    fixed = fixed.replace(
        'sep_layout.setContentsMargins(8, 0, 8, 0)',
        'sep_layout.setContentsMargins(scale(8), 0, scale(8), 0)'
    )

    # 5. coll_header.setContentsMargins(8, 0, 4, 4)
    fixed = fixed.replace(
        'coll_header.setContentsMargins(8, 0, 4, 4)',
        'coll_header.setContentsMargins(scale(8), 0, scale(4), scale(4))'
    )

    # 6-9. font-size replacements
    fixed = re.sub(r'font-size:\s*9px', '{sfs(9)}', fixed)
    fixed = re.sub(r'font-size:\s*11px', '{sfs(11)}', fixed)
    fixed = re.sub(r'font-size:\s*13px', '{sfs(13)}', fixed)
    fixed = re.sub(r'font-size:\s*8px', '{sfs(8)}', fixed)

    # 10. add_btn.setFixedSize(18, 18)
    fixed = fixed.replace(
        'add_btn.setFixedSize(18, 18)',
        'add_btn.setFixedSize(scale(18), scale(18))'
    )

    # 11. btn.setFixedHeight(20)
    fixed = fixed.replace(
        'btn.setFixedHeight(20)',
        'btn.setFixedHeight(scale(20))'
    )

    # 12. icon_label.setFixedWidth(14)
    fixed = fixed.replace(
        'icon_label.setFixedWidth(14)',
        'icon_label.setFixedWidth(scale(14))'
    )

    # 13. arrow_btn.setFixedSize(14, 16)
    fixed = fixed.replace(
        'arrow_btn.setFixedSize(14, 16)',
        'arrow_btn.setFixedSize(scale(14), scale(16))'
    )

    # 14. spacer.setFixedSize(14, 16)
    fixed = fixed.replace(
        'spacer.setFixedSize(14, 16)',
        'spacer.setFixedSize(scale(14), scale(16))'
    )

    # 15. color_chip.setFixedSize(8, 8)
    fixed = fixed.replace(
        'color_chip.setFixedSize(8, 8)',
        'color_chip.setFixedSize(scale(8), scale(8))'
    )

    # 16. setSpacing(8)
    fixed = fixed.replace(
        'layout.setSpacing(8)',
        'layout.setSpacing(scale(8))'
    )

    # 17. setSpacing(2)
    fixed = fixed.replace(
        'inner.setSpacing(2)',
        'inner.setSpacing(scale(2))'
    )

    # 18. layout.setContentsMargins(8, 0, 8, 0) in _create_item
    fixed = re.sub(
        r'(\n\s+layout = QtWidgets\.QHBoxLayout\(btn\)\n\s+)layout\.setContentsMargins\(8, 0, 8, 0\)',
        r'\1layout.setContentsMargins(scale(8), 0, scale(8), 0)',
        fixed
    )

    # 19. inner.setContentsMargins(indent, 0, 8, 0)
    fixed = fixed.replace(
        'inner.setContentsMargins(indent, 0, 8, 0)',
        'inner.setContentsMargins(indent, 0, scale(8), 0)'
    )

    # Reconstruct the full content
    new_content = content[:class_start] + fixed + content[class_start + len(class_content):]

    with open(file_path, 'w') as f:
        f.write(new_content)

    print("Fixed scaling in CollectionListWidget")

if __name__ == '__main__':
    fix_scaling('/Users/tom/Documents/GitHub/sopdrop/packages/sopdrop-houdini/scripts/sopdrop_library_panel.py')
