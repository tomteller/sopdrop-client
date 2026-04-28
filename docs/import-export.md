# Import & Export System

How Sopdrop serializes and deserializes Houdini node networks.

## Package Formats

### V2 (Binary, Default)

- Format field: `"sopdrop-v2"` or `"chopsop-v2"`
- Uses `saveItemsToFile()` / `loadItemsFromFile()` (Houdini's native cpio)
- Data stored as base64 in `package["data"]`, SHA256 in `package["checksum"]`
- Preferred: preserves exact node state, parameters, expressions
- Falls back to V1 if `saveItemsToFile()` is unavailable (old Houdini, Apprentice)

### V1 (Python Code, Fallback)

- Format field: `"sopdrop-v1"` or `"chopsop-v1"`
- Uses `node.asCode()` to generate Python that recreates nodes
- Code stored in `package["code"]`, SHA256 in `package["checksum"]`
- Inspectable and auditable, but fragile with name collisions
- Each node wrapped in a scoped function `_sdrop_create_N()` to isolate asCode's variable mutations

### Other Formats

- `"sopdrop-hda-v1"` — HDA binary (`.hda` file base64-encoded)
- `"sopdrop-vex-v1"` — VEX snippet (code string, no Houdini serialization)
- `"sopdrop-path-v1"` — File path reference (HDRI, texture)
- `"sopdrop-curves-v1"` — Animation keyframe curves (uses V2 cpio under the hood, with extra metadata: channel names, keyframe counts, frame range)

## Export Flow (`export.py`)

### Entry Point: `export_items(items)`

```
export_items(items)
  ├── try: _export_v2(items)     # Binary cpio
  └── except: _export_v1(items)  # Python asCode fallback
```

Both paths:
1. Validate items with `_validate_items()` — calls `item.name()` to detect dead objects
2. Classify into nodes, network boxes, sticky notes, dots
3. Verify all items share the same parent
4. Extract context via `_get_context(parent)`
5. Detect HDA dependencies with `_detect_hda_dependencies(nodes)`
6. Capture node graph for web visualization with `_capture_node_graph()`

### V2 Export (`_export_v2`)

1. Call `parent.saveItemsToFile(items, temp_path)` to write cpio
2. Read binary, base64-encode, SHA256 checksum
3. Return package with `format: "sopdrop-v2"`, `data`, `checksum`

### V1 Export (`_export_v1`)

1. For each node: call `asCode(brief=True, recurse=True, ...)`
2. Wrap in function `_sdrop_create_N()` to isolate scope (prevents variable leaking between nodes)
3. Export network boxes: `createNetworkBox()`, position, size, color, comment
4. Export sticky notes: `createStickyNote()`, all visual properties
5. Export network dots: `createNetworkDot()`, wire inputs (handles chained dots)
6. Generate connections in two passes:
   - Pass 1: Direct node-to-node wiring
   - Pass 2: Override with dot-routed connections (position-based heuristic matching)
7. Add items to network boxes (deferred until all items exist)
8. Normalize paths: absolute `hou.node('/obj/geo1/...')` → relative `hou_parent.node('...')`
9. SHA256 checksum the normalized code

### Container HDA Detection

When a single selected node is a custom HDA that is also a subnet with children (like SOP Create), the save dialog:
- Replaces `self.items` with the children (`node.allItems()`)
- Stores HDA info as `container_hda` metadata
- Exports the children (not the container) as a regular node package
- On import, the container is reconstructed (see below)

Detection in `SaveToLibraryDialog.__init__`:
```python
if node.isSubNetwork() and node.children():
    self.items = list(node.allItems())      # Replace with children
    self.container_hda = self.hda_info      # Store container info
    self.hda_info = None                    # Not an HDA binary save
```

## Import Flow (`importer.py`)

### Entry Point: `import_items(package, target_node, position)`

```
import_items(package, target_node, position)
  ├── wraps in hou.undos.group("Sopdrop Paste")
  ├── if format == "sopdrop-v1": _import_v1(...)
  └── else: _import_v2(...)
```

Convenience wrapper: `import_at_cursor(package)` — calculates cursor or view center position, then calls `import_items()`.

### V2 Import (`_import_v2`)

1. Validate context matches target
2. Check for missing HDA dependencies (mandatory error, no placeholders)
3. **Size guard**: Reject packages > 667 MB base64 (~500 MB decoded) to prevent OOM
4. Base64-decode, verify SHA256 checksum
5. Write to temp file via `tempfile.mkstemp()` (atomic, no TOCTOU race)
6. **Container HDA reconstruction** (if `metadata.container_hda` exists):
   - Create the container node (`target_node.createNode(type_name)`)
   - Clear its default children
   - Load saved children into the container
7. Call `loadItemsFromFile()` on the target (or container)
8. Detect new items via before/after set comparison + return value
9. Filter network boxes into top-level vs nested (see below)
10. Reposition and select

### V1 Import (`_import_v1`)

1. Validate context matches target
2. Check for missing HDAs (optional placeholder mode with red subnets)
3. Patch old-format code if needed (`_patch_old_format_code()`)
4. **Container HDA reconstruction** (same logic as V2)
5. Execute code via `exec()` with namespace `{"hou": hou, "hou_parent": target_node}`
6. **Retry on failure**: If execution raises, wrap `.setInput()`/`.addItem()` calls in try/except and retry
7. **Cleanup on retry**: Only destroy top-level items (direct children of target), never arbitrary order
8. Reposition and select

### Retry Logic (V1 Only)

When code execution fails:

```
Failure detected
  ├── Count partial items created
  ├── If 0 items or <10% of expected: cleanup + retry with resilient connections
  ├── If >=10% created: continue with partial result, warn user
  └── If retry also fails: raise ImportError
```

Cleanup only destroys top-level items (direct children of `target_node`). Destroying in arbitrary order can segfault because destroying a parent auto-destroys its children.

## Container HDA Reconstruction

When `metadata.container_hda` is present with `type_name`:

```python
# 1. Create container in target network
container_node = target_node.createNode(type_name)

# 2. Verify it's a subnet (can hold children)
if container_node.isSubNetwork():
    # 3. Remove default children
    for child in list(container_node.children()):
        child.destroy()

    # 4. Load package contents INTO the container
    load_target = container_node

    # 5. After loading, position only the container
    # 6. Call layoutChildren() on container internals
    # 7. Return [container_node] as result
```

This ensures nodes exported from inside a SOP Create (or similar container HDA) are properly wrapped when pasted back.

## Network Box Positioning

### The Problem

Network boxes can be nested. `box.move(offset)` moves the box AND its children. If we also independently move the children, they get double-moved.

### The Solution

Before repositioning, classify boxes:

```python
for netbox in new_netboxes:
    parent_box = netbox.parentNetworkBox()
    if parent_box is not None and parent_box in new_netbox_set:
        nested_netboxes.append(netbox)   # Skip — parent will move it
    else:
        top_level_netboxes.append(netbox) # Move independently
```

Similarly, nodes inside network boxes are excluded from independent repositioning since the box moves them.

### Bounding Box & Reposition (`_reposition_items`)

1. Calculate bounding box of all items (nodes, stickies, netboxes)
2. Use **saved position/size data** captured at load time (before any operations)
3. Compute offset to center bounding box on target position
4. Move loose items (nodes, stickies not in boxes)
5. Move top-level network boxes (children ride along automatically)
6. Restore network box sizes (Houdini may resize during load)

## Key Differences: V1 vs V2

| Aspect | V1 (asCode) | V2 (cpio binary) |
|--------|-------------|-------------------|
| Execution | `exec()` Python code | `loadItemsFromFile()` |
| Inspectable | Yes | No |
| Placeholder HDAs | Yes (red subnets) | No (mandatory error) |
| Retry on failure | Yes (resilient connections) | No (atomic) |
| Name collision risk | Higher | None |
| Container HDA support | Yes | Yes |
| Size guard | None | 500 MB max |
| Temp file creation | `tempfile.mkstemp()` | `tempfile.mkstemp()` |

## Exception Classes

| Class | Parent | Purpose |
|-------|--------|---------|
| `ImportError` | Exception | Base import failure |
| `ChecksumError` | ImportError | SHA256 mismatch |
| `ContextMismatchError` | ImportError | Package context != target |
| `MissingDependencyError` | ImportError | Required HDA type not found |
