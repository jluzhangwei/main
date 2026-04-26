# LLDP Topology Tool: Functional Design and Implementation Notes

## Documentation Plan

This document explains the LLDP topology tool by functional workflow rather than by UI widgets. The goal is to make the design understandable for technical review, project reporting, and future maintenance.

1. Define the problem the tool solves: multi-source network adjacency collection, automatic topology generation, link performance analysis, and visual operations.
2. Explain how data enters the system: CLI, SQL, NDMP, CSV file/paste, and Json session import.
3. Explain how topology is generated: field inference, device identity normalization, link deduplication, position preservation, layout, and filtering.
4. Explain topology editing: selection, deletion, restore, undo/redo, manual nodes, manual links, and link descriptions.
5. Explain node grouping: group creation, group color, group view, isolated group-view coordinates, and removing devices from groups.
6. Explain link analysis: CLI/Zabbix utilization collection, bandwidth detection, color analysis, load balancing analysis, and label controls.
7. Explain export, state persistence, performance optimization, and reliability design.

## 1. Project Positioning

The LLDP topology tool is designed to convert device adjacency, port information, link bandwidth, link utilization, and manual operational annotations into an interactive network topology. It is not just a drawing tool. It is a data collection, topology computation, link analysis, and state management tool for network operations.

The traditional workflow usually requires engineers to log in to devices manually, copy LLDP output, clean data in spreadsheets, and then draw the topology by hand. This tool automates that workflow:

- Collect data from devices, databases, NDMP APIs, CSV files, and Json sessions.
- Detect devices, ports, neighbors, and IP addresses automatically.
- Generate topology nodes and links automatically.
- Preserve user-adjusted node positions.
- Organize topology by business domain, hierarchy, partition, and name similarity.
- Display link utilization and bandwidth.
- Support node groups, path search, link descriptions, and manual link completion.
- Export to PNG, draw.io, Mermaid, link summary CSV, and Json sessions.

## 2. Overall Data Model

Internally, all import modes are normalized into the same type of LLDP row data. The standard fields include:

- `depth`: recursion depth.
- `localhostname`: local device hostname.
- `ipaddr` / `sourceip`: local device IP address.
- `localinterface`: local interface.
- `remotehostname`: neighbor device hostname.
- `remoteinterface`: neighbor interface.
- `remotevendor`: neighbor vendor.
- `remoteip`: neighbor IP address.

After all sources are normalized into the same fields, the frontend graph pipeline does not need to care whether the data came from CLI, SQL, NDMP, or CSV. Every source eventually enters the same pipeline:

1. Parse the input.
2. Infer column mappings.
3. Merge or replace existing rows.
4. Deduplicate rows and links.
5. Generate nodes and edges.
6. Preserve existing coordinates and view state.
7. Refresh the topology.

The value of this design is that a new data source only needs to output the standard CSV fields. Once it does that, it can reuse the existing topology generation, filtering, grouping, link analysis, and export capabilities.

## 3. Import Functions

### 3.1 CLI Import

CLI import collects real-time LLDP adjacency directly from network devices. It is useful when there is no complete database, when the operator needs to verify live connectivity, or when the latest topology state is required.

#### Access Methods

CLI import supports multiple access paths:

- Direct SSH: log in to the target device directly.
- SMC jump host: connect to the SMC shell first, then SSH from SMC to the target device.
- PAM/jump workflow: complete a two-stage login using a command template and credentials.

The UI can configure:

- Start device address.
- Recursion depth.
- Query concurrency.
- Whether recursion should be limited to `172.*` addresses.
- Whether ping precheck should be enabled.
- SSH username and password.
- SMC host, port, and command template.
- Login timeout.
- Command timeout.

#### Ping Precheck

CLI import can enable ping precheck. The logic is:

1. Ping the target device with 2 packets.
2. If any packet succeeds, the device is considered reachable and SSH login is attempted.
3. If both packets fail, the device is skipped to avoid long SSH connection waits.

This option is useful when large recursive queries include many unreachable devices. If the network blocks ICMP, the option can be disabled.

#### Login and Timeout Control

The login stage is controlled by:

- SSH `ConnectTimeout`.
- Interactive login timeout.
- Command execution timeout.
- No repeated retries for failed devices.

The current design prefers fast failure. A single unreachable or abnormal device should not block the entire topology collection job.

#### Pagination Disablement

After login succeeds, the tool tries to disable terminal pagination before vendor detection and LLDP collection.

This is important because some Cisco/NX-OS devices may return `--More--` during `show version`. If pagination is not disabled first, the worker may spend a long time waiting for more output.

The tool tries commands such as:

- Huawei: `screen-length 0 temporary`
- Cisco/NX-OS/Arista: `terminal length 0`

After vendor detection, the tool may run the vendor-specific pagination command again to ensure subsequent LLDP output is complete.

#### Vendor Detection

CLI import automatically detects the device vendor through lightweight version commands:

- Huawei: `display version` / `dis version`
- Cisco/NX-OS: `show version`
- Arista: `show version`

The detected vendor controls command selection, LLDP parsing, device-name detection, and utilization collection.

#### Device Name Detection

Device name detection follows this priority:

1. Extract the device name directly from the prompt.
2. For Cisco, prefer `show hostname`.
3. Fall back to `show version` / `show ver`.
4. For Huawei, use `display current-configuration | include  sysname` or `dis current-configuration | include  sysname`.

This order avoids slow commands such as `show running-config | include ^hostname` on some Cisco devices. Prompt extraction and lightweight hostname commands reduce per-device detection latency.

#### LLDP Commands and Parsing

The LLDP command and parser are selected by vendor:

- Huawei: supports both brief tables and detailed output.
- Cisco/NX-OS/IOS-XR: supports `show lldp neighbors` and detailed output.
- Arista EOS: supports Arista-style LLDP detail output.

The parser extracts:

- Local device name.
- Local interface.
- Neighbor device name.
- Neighbor interface.
- Neighbor IP address.
- Neighbor vendor.
- Recursion depth.

Huawei breakout-style ports such as `100GE1/0/17:0` are preserved as-is so that physical link identity is not lost.

#### Recursive Polling

Recursive CLI collection progresses layer by layer:

1. Depth 0 queries the start device.
2. Neighbor IPs from depth 0 become the next query set.
3. Depth 1 queries those neighbors in parallel.
4. The process continues until the configured recursion depth is reached.

The recursion engine maintains:

- Visited device set.
- Current-depth device set.
- Next-depth discovered devices.
- Per-device timing.
- Per-depth summary.
- Failed device list.

If `recursive only 172.*` is enabled, only neighbor IPs beginning with `172.` are added to the next recursion layer. This prevents management IPs, server IPs, and cross-domain addresses from expanding the topology unexpectedly.

#### Concurrency Control

CLI concurrency is user-configurable and bounded to a reasonable range to avoid overloading jump hosts or network devices.

The running status shows:

- Completed device count.
- Current depth.
- Current concurrency.
- Elapsed time.
- Current device.
- Total planned devices when available.

This helps the user distinguish whether a slow job is caused by too many devices, low concurrency, one slow device, or a large recursion layer.

#### Deduplication

CLI results are deduplicated in two ways:

1. Row-level deduplication based on `depth + local device + local interface + neighbor device + neighbor interface`.
2. Device identity normalization, so that the same physical device does not appear once as an IP node and once as a hostname node.

This prevents duplicate topology nodes and duplicate physical links.

#### Debug Logs

CLI collection produces debug logs for troubleshooting. The logs include:

- Per-depth summary: submitted devices, completed devices, discovered next-hop devices, and elapsed time.
- Per-device timing: device, depth, vendor, neighbor count, duration, and error.
- Raw command send/receive snippets.

When a device is slow, the debug log helps determine whether it is slow during login, vendor detection, hostname detection, LLDP command execution, or parsing.

### 3.2 SQL Import

SQL import generates topology from an existing LLDP database. It is suitable when a scheduled collection platform already stores LLDP records.

#### Query Model

The user enters a start hostname. The backend queries the latest LLDP adjacency records for that device.

SQL import is not a single-device lookup. It performs a BFS-like recursive query:

1. Use the start device as the first query set.
2. Query the latest LLDP records for the current device set.
3. Use returned neighbor hostnames as the next query set.
4. Continue until the maximum depth is reached.

#### Latest Data Selection

SQL import uses the latest `create_time` for each device to avoid mixing historical links into the current topology.

This is important because network topology changes frequently. If historical LLDP records are mixed with current records, the graph may show links that no longer exist.

#### Alias and IP Normalization

SQL import can expand aliases based on recent IP mappings. If a device has multiple names or IP records in the database, the query tries to include the relevant aliases in the same query scope.

This reduces missed links caused by inconsistent naming.

#### Cancel Capability

SQL import is task-based:

- Create a task.
- Poll task status from the frontend.
- Allow the user to cancel the task.

If a database query becomes slow, the user can cancel it without restarting the service.

### 3.3 NDMP Import

NDMP import calls an interface inventory API and extracts neighbor data from interface fields.

The API pattern is:

```http
POST /apis/ndmp/v2/device_interface/query
```

The request can use either:

- `device_hostname`
- `device_ip`

#### Query Modes

The UI supports:

- Auto mode.
- Hostname mode.
- IP mode.

If hostname lookup fails, the user can switch to IP mode. If the input is already an IP address, IP mode can be used directly.

#### Recursive Control

NDMP import supports recursion as well:

1. Query the start device's interfaces.
2. Extract neighbor hostname or neighbor IP from interface fields.
3. Query the next layer.
4. Stop when the configured depth is reached.

The recursion limit is intentionally low to avoid excessive API fan-out.

#### Link Status Filter

NDMP import supports an optional `link_status == up` filter.

- Enabled: only current up links are imported, suitable for current topology generation.
- Disabled: historical or down links may also be imported, useful for troubleshooting and historical comparison.

#### Flexible Field Extraction

NDMP responses may use different field names or nested structures. The backend attempts to extract data from multiple possible fields:

- Local hostname.
- Local IP address.
- Local interface.
- Neighbor hostname.
- Neighbor IP address.
- Neighbor interface.
- Neighbor vendor.
- Link status.

If the interface list is nested under fields such as `data`, `result`, `payload`, `items`, or `rows`, the parser attempts to unwrap it.

### 3.4 CSV File Import and Paste Import

CSV import is used for manual completion, third-party exports, or data prepared in spreadsheets.

It supports two modes:

- Select a CSV file.
- Paste CSV text directly.

#### Header Detection

The import parser detects headers and infers the following columns:

- Local device.
- Neighbor device.
- Local interface.
- Neighbor interface.
- Local IP.
- Neighbor IP.

It supports comma, tab, and semicolon delimiters, and it handles quoted fields.

#### Append Logic

CSV can be imported as a new topology or appended to the current topology.

When appending, the tool:

1. Preserves current node coordinates.
2. Parses the new CSV.
3. Builds a logical link key from endpoints and interfaces.
4. Replaces existing links if the key already exists.
5. Adds new links if the key is new.
6. Regenerates the topology while preserving existing visible positions as much as possible.

This is useful for manually adding a missing link discovered during troubleshooting.

### 3.5 Json Import

Json import restores a complete tool session.

A Json session contains more than raw rows. It can include:

- Raw LLDP rows.
- Column mappings.
- Node coordinates.
- Deletion state.
- Node groups.
- Group-view coordinates.
- Manual nodes.
- Manual links.
- Link descriptions.
- Custom node colors.
- UI settings.

Json supports two workflows:

- Import: replace the current workspace.
- Append: merge the Json data into the current workspace.

When appending Json, duplicate nodes should keep their current positions in the active view. New nodes are selected after import so the user can drag them into the desired area.

## 4. Topology Generation

### 4.1 Graph Inputs

Topology generation uses multiple input sources:

- Standard LLDP rows.
- Manually added nodes.
- Manually added links.
- Node group definitions.
- Deletion state.
- Filter state.
- Utilization cache.
- Custom colors.
- Link descriptions.

The graph is not generated from CSV rows alone. It is generated from raw data plus all current visual and operational state.

### 4.2 Device Identity Normalization

A device may appear in different forms:

- Hostname.
- IP address.
- FQDN.
- Multiple management addresses.
- Neighbor side returns only IP, while later collection discovers the hostname.

The tool normalizes identities by:

- Stripping domain suffixes.
- Normalizing case and whitespace.
- Preferring readable hostnames for labels.
- Merging IP and hostname references when they point to the same device.
- Showing multiple addresses in the address field instead of creating duplicate nodes.

This is important in mixed Arista, Cisco, and Huawei networks because neighbor fields are not always returned in the same format.

### 4.3 Link Identity and Deduplication

A physical link is mainly identified by:

- Local device.
- Local interface.
- Neighbor device.
- Neighbor interface.

The tool attempts to recognize that A-to-B and B-to-A records may describe the same physical link. This avoids showing duplicate lines for the same link.

If two devices have multiple links through different interfaces, those links remain separate physical links and are not incorrectly collapsed.

### 4.4 Multi-Link Aggregation

When multiple links exist between the same two devices, the tool supports two display modes:

- Aggregate multi-links: show them as one grouped edge with summarized ports, bandwidth, and utilization.
- Show individual links: display every physical link separately.

Aggregation only affects visualization. The original per-link data is still preserved internally.

### 4.5 Coordinate Preservation

Graph generation prioritizes existing coordinates:

- Existing nodes keep positions adjusted by the user.
- Json import restores saved positions.
- Json append keeps duplicate nodes at current positions.
- New nodes use imported coordinates or layout-generated coordinates.

This design prevents large graphs from shifting unexpectedly after every data update. The implementation updates Cytoscape elements incrementally instead of destroying and rebuilding the entire graph every time.

### 4.6 Generate Topology

The `Generate Topology` action rebuilds the graph based on the current workspace and layout options.

In the main view, it applies to the whole topology.

Inside a node group view, it should only affect the current group-view scope and should not overwrite the main topology coordinates.

## 5. Layout and Hierarchy

### 5.1 Force-Directed Layout

Force-directed layout is suitable for unknown structures or first-time imports. It uses node repulsion, edge length, component spacing, and overlap avoidance to spread the topology automatically.

The dispersion strength affects:

- Node repulsion.
- Edge length.
- Component spacing.
- Layout padding.
- Overlap avoidance.

A larger value spreads the graph more. A smaller value makes the graph more compact but may increase label overlap.

### 5.2 Hierarchical Layout

Hierarchical layout places devices into layers based on keywords. Example roles include:

- Core
- SuperSpine
- Spine
- Border
- Leaf
- ToR

The hierarchy keyword expression supports:

- Comma: separate layers.
- Slash or pipe: multiple keywords in the same layer, matching any one.
- `&` or `+`: all keywords must match.

Example:

```text
CORE,SUPERSPINE,SPINE,BORDER,LEAF/TOR
```

This means Core is placed above SuperSpine, SuperSpine above Spine, and Leaf/ToR can be placed in the same layer.

### 5.3 Partition Layout

Partition keywords split nodes within the same layer by business domain or region. Examples:

- LAN
- WAN
- OOB
- IDC
- DCI

Partition expressions use the same grammar as hierarchy expressions. Matching is evaluated from left to right, and the first matched partition wins.

### 5.4 Name-Similarity Clustering

Name-similarity clustering keeps similarly named devices close to each other.

The similarity score considers:

- Left-to-right common prefix.
- Token similarity after splitting by `-`.
- Plane keywords such as LAN, WAN, OOB, and MGMT.
- Numeric closeness such as R001 and R003.
- Role keywords such as Spine, Leaf, and Core.

For example:

```text
TH-THSTT1-Garena-LAN-R001-Spine-01
TH-THSTT1-Garena-LAN-R003-Spine-02
```

are considered closer than:

```text
TH-THSTT1-Garena-LAN-R001-Spine-01
TH-THSTT1-Garena-WAN-R001-Spine-01
```

because the first pair has stronger left-to-right structural similarity.

### 5.5 Hide and Show-Only Filters

`Hide` and `Show only` are view filters. They do not delete raw data.

The matching logic normalizes text by:

- Ignoring case.
- Supporting both Chinese and English input.
- Normalizing full-width and half-width characters.
- Normalizing different hyphen characters.
- Matching both original text and punctuation-stripped text.

Therefore, entering `OOB` should match names containing `-OOB`.

The filter checks:

- Full node label.
- Short node label.
- Node IP.
- Group member labels.
- Group labels.

### 5.6 172.* Network Filter

The tool has two different `172.*` concepts:

- Show only `172.*`: frontend view filtering only; raw data is unchanged.
- Recursively query only `172.*`: collection-time control that affects what data is collected.

The difference is important. The first changes what is visible; the second changes what is collected.

## 6. Node Operations

### 6.1 Device Selection

Users can click a node or box-select multiple nodes. Selection is used by:

- Delete.
- Keep selected.
- Keep adjacent only.
- Device grouping.
- Alignment operations.
- Manual link creation.
- Custom node color.
- Default source/target for path search.

### 6.2 Device Locator

The device locator helps find nodes in large graphs. It uses the same expression grammar as filters:

- Comma for groups.
- Slash for OR matching.
- `&` for AND matching.
- Chinese/English text normalization.
- Punctuation-insensitive matching.

Locator does not hide other nodes. It highlights matched devices and moves the viewport to them.

### 6.3 Delete and Restore

Deleting a device does not remove raw data. The node is added to a deletion stack.

Restoring deleted devices pops them from the deletion stack. This lets users clean the view temporarily without destroying imported data.

### 6.4 Undo and Redo

The page supports up to 5 undo/redo steps.

Snapshots include:

- Raw rows.
- Coordinates.
- Deletion stack.
- Filter state.
- Node groups.
- Manual nodes.
- Manual links.
- Link descriptions.
- Custom colors.
- Selection state.

Operations such as alignment, movement, deletion, and grouping should be captured in undo history.

### 6.5 Single-Link Nodes

The single-link node feature finds devices that have exactly one visible neighbor in the current view.

The logic is:

1. Use only currently visible nodes and edges.
2. Count unique visible neighbors for each node.
3. If a node has exactly one neighbor, mark it as a single-link node.
4. If a node group contains a single-link member, the group node is also highlighted.

This feature selects and highlights nodes only. It does not select links.

### 6.6 Alignment and Distribution

Alignment features help manually organize local topology areas. Supported actions include:

- Horizontal align.
- Vertical align.
- Left align.
- Right align.
- Horizontal distribute.
- Vertical distribute.

These actions apply only to selected nodes, update the coordinate state, and support undo.

## 7. Manual Completion Features

### 7.1 Add Normal Node

The context menu can add a normal node. Normal nodes are useful for conceptual devices, missing devices, or temporary annotations.

Manual nodes are stored in `manualCanvasNodes` and participate in:

- Dragging.
- Selection.
- Manual link creation.
- Json export.
- Json import recovery.

### 7.2 Add Cloud Node

Cloud nodes represent external networks, Internet, third-party networks, carrier clouds, or unknown domains.

Cloud nodes differ from regular devices:

- They use a cloud icon.
- The name can be edited.
- They are used for conceptual diagrams.
- They do not participate as real LLDP devices in recursive collection.

Cloud nodes are also stored in Json sessions and should recover after refresh or Json import.

### 7.3 Add Manual Link

When exactly two nodes are selected, the context menu can add a link between them.

The modal asks for:

- Source interface.
- Destination interface.

After saving, the tool creates a standard LLDP row and appends it to the current workspace. This means manual links use the same graph generation logic as collected links.

### 7.4 Link Description

A single link can have a description added from the context menu.

The description is stored as link metadata and can be displayed through the `Show link description` checkbox.

For an aggregated edge, the description can be bound to the device pair. For an individual link, the description is bound to the specific port-level link.

The description is displayed together with port labels, bandwidth, and utilization so that the annotation stays attached to the link information.

## 8. Node Groups

### 8.1 Device Grouping

Node groups collapse multiple devices into one logical node. They are useful for large topologies with regions, data centers, pods, business domains, or repeated structures.

Supported grouping operations:

- Group multiple regular nodes.
- Group regular nodes with existing group nodes.
- Keep the existing name when a single group is selected.
- Recalculate the group name when multiple groups are merged.

Grouped nodes use yellow by default to avoid conflict with regular node colors and single-link red markers.

### 8.2 Group Name Calculation

When multiple devices are grouped, the tool derives a group name from common parts of device names.

For example, if multiple devices share:

```text
TH-THSTT1-Garena-LAN
```

then the group name should preserve that meaningful business prefix instead of producing a random label.

The user can also rename the group manually.

### 8.3 Device Count in Group

The group node can show the number of devices inside the group.

This allows users to quickly understand how many real devices a collapsed node represents.

### 8.4 Link Handling After Grouping

After grouping, external links from group members are remapped to the group node.

If multiple members connect to the same external device, the group node may show an aggregated edge to that external device. The original member-level links are not lost; they are only collapsed visually.

### 8.5 Enter Group View

Double-clicking a group node enters group view.

Group view contains:

- All devices inside the group.
- One-hop neighbors of group members.
- Links related to group members.

This allows focused inspection of a group without expanding the entire main topology.

### 8.6 Isolated Group-View Coordinates

Main view and group view use separate coordinate sets.

The reason is that the same devices serve different purposes in different views. In the main view, positions support the overall topology. In group view, positions support local analysis. If both views shared one coordinate set, editing group view would damage the main topology layout.

Current design:

- Main view stores one coordinate map.
- Each group view stores its own scope coordinate map.
- Entering group view loads that group's own coordinates.
- Exiting group view saves group-scope coordinates without overwriting main-view coordinates.
- Json export includes both main-view and group-view coordinates.

Old Json files without group-view coordinates can still be imported. The system initializes group view from main-view coordinates if no group-scope coordinates exist.

### 8.7 Context Menu Inside Group View

Inside group view, right-clicking a device supports:

- Return to main view.
- Remove from group.
- Copy name/address.
- Current utilization query.
- Time-range utilization query.
- Custom color.

Removing a device from a group updates the group membership. After returning to the main view, the removed device should not reappear inside the original group.

## 9. Node Colors

### 9.1 Default Role Colors

The tool can apply base colors based on device role keywords, such as:

- Core.
- SuperSpine.
- Spine.
- Border.
- Leaf.
- ToR.
- SBB.

This helps users identify device roles without reading every full device name.

### 9.2 Keyword-Based Color Rules

Node colors can be defined by keyword rules, for example:

```text
SUPERSPINE:#4f46e5
SPINE:#2563eb
BORDER:#06b6d4
LEAF:#16a34a
TOR:#22c55e
CORE:#7c3aed
SBB:#64748b
```

When a device name contains a keyword, the corresponding color can be applied.

### 9.3 Custom Color from Context Menu

The user can select one or multiple nodes and set a custom color from the context menu.

Custom colors have higher priority than default role colors, but they should not override special status colors:

- Group node yellow.
- Single-link node red.
- Selection border.

This prevents semantic warning colors from being hidden by business color rules.

## 10. Path Search

### 10.1 Purpose

Path search finds paths between two devices in the current visible topology.

It does not call the backend and does not collect new data. It computes paths based only on currently visible Cytoscape nodes and edges.

### 10.2 Search Conditions

The path modal supports:

- Source node.
- Destination node.
- Required transit nodes.
- Backup path count.

If exactly two nodes are selected, they are prefilled as source and destination. If three or more nodes are selected, the first two become source and destination, and the rest become transit nodes.

### 10.3 Search Rules

The graph is treated as undirected. The default cost is hop count.

Transit nodes are chained in the input order:

```text
source -> transit1 -> transit2 -> destination
```

If any segment is unreachable, the result clearly indicates which segment failed.

### 10.4 Equal-Length Main Paths

The main path can contain more than one equivalent shortest path.

If two shortest paths have the same hop count, the result shows:

- Main path.
- Main path 2.

This prevents the UI from hiding an equally valid path simply because the algorithm found another one first.

### 10.5 Backup Paths

Backup path count supports 0, 1, or 2.

Backup paths prefer avoiding edges already used by previous paths. This is edge-level avoidance, not a strict disjoint-path algorithm. It does not guarantee full node or link independence.

### 10.6 Keep Only Path Nodes

The path modal can keep only nodes that belong to the current path result.

The logic is:

1. Collect all nodes in the path result.
2. Delete currently visible nodes that are not part of the result.
3. Push deleted nodes into the deletion stack.
4. Allow the user to restore them later.

This is useful for extracting a path segment from a large graph for analysis or export.

### 10.7 Set as Transit Node

When a path result exists, a node can be set as a transit node from the context menu and the path can be recalculated.

This allows the workflow to evolve from “find any path” to “find a path that must pass through this device.”

## 11. Link Utilization Analysis

### 11.1 Purpose

Link utilization analysis turns the topology from a connectivity diagram into an operational status map.

The tool can display:

- TX utilization.
- RX utilization.
- Time-range maximum.
- Time-range minimum.
- Total bandwidth.
- Load balancing difference.
- Congestion color.

### 11.2 CLI Utilization Collection

CLI utilization collection logs into devices and queries interface status.

Different vendors use different commands:

- Huawei: parse input/output rate and bandwidth from interface output.
- Cisco/NX-OS: parse input/output rate, load, and bandwidth from interface details.
- Arista: parse rate and bandwidth from show interface output.

The parser extracts:

- TX percentage.
- RX percentage.
- TX bps.
- RX bps.
- Interface bandwidth.

If the device output does not provide percentage directly but provides bps and bandwidth, the tool computes:

```text
utilization = current bps / interface bandwidth * 100
```

### 11.3 Zabbix Utilization Collection

Zabbix collection retrieves current or historical utilization from the monitoring system.

Configuration includes:

- Zabbix URL.
- Token.
- SSL verification option.

The configuration is stored locally on the server, so the user does not need to re-enter it every time.

### 11.4 Zabbix Host Matching

Zabbix host matching tries multiple strategies:

1. Match by device IP through host interfaces.
2. Match by device hostname through host/name search.
3. If multiple candidate hosts exist, choose the best one by data completeness.

The best candidate is selected by checking:

- Whether speed items exist.
- Whether TX items exist.
- Whether RX items exist.
- Whether the host matches the preferred IP.
- Whether the host has a richer item set.

### 11.5 Interface Name Compatibility

Interface names may differ between topology data and Zabbix items. The tool expands aliases such as:

- `Eth1/1` and `Ethernet1/1`
- `Gi` and `GigabitEthernet`
- `Te` and `TenGigE`
- `Hu` and `HundredGigE`
- `BE` and `Bundle-Ether`
- `Po` and `Port-Channel`
- `Lo` and `Loopback`
- `MEth` and `MgmtEth`

This addresses cases where the topology has the correct port, but Zabbix uses a different naming convention.

### 11.6 Time-Window Query

Zabbix supports:

- Current value.
- Time-range maximum.
- Time-range minimum.

For time-range queries, the backend chooses data source based on the time span:

- Longer spans prefer trend data.
- Shorter spans or missing trend data fall back to history data.

This adapts to Zabbix data compression. If recent history is unavailable but older trend data exists, the query can still return a value.

### 11.7 Bandwidth Detection

Bandwidth is determined in this priority order:

1. Existing cache for the interface.
2. Zabbix speed item.
3. Longer-window speed fallback.
4. Peer-side speed item.
5. Interface-name inference, such as `100GE`, `400GE`, or `TenGigE`.

This reduces repeated bandwidth queries and allows one side of a link to fill missing bandwidth for the other side.

### 11.8 Utilization Metric

Available metrics include:

- TX.
- RX.
- MIN.
- MAX.

Meaning:

- `Time-range maximum` returns the maximum utilization within the selected time window.
- `Time-range minimum` returns the minimum utilization within the selected time window.

MIN and MAX should not be mixed. They are queried and aggregated separately.

### 11.9 TX/RX Direction

TX is interpreted according to the link arrow direction.

If the link is:

```text
A -> B
```

then TX means traffic sent from A's interface toward B, and RX means traffic received by A's interface.

The UI can display TX and RX together to support bidirectional link analysis.

### 11.10 Total Bandwidth Display

Total bandwidth display does not depend on the utilization label checkbox. The user can display bandwidth without showing utilization percentage.

If multi-link aggregation is enabled, total bandwidth is the sum of all physical links between the two devices.

If multi-link aggregation is disabled, each physical link displays its own bandwidth.

### 11.11 Link Congestion Analysis

`Link Congestion Analysis` colors links according to utilization:

- Green: low utilization.
- Yellow: medium utilization.
- Red: high utilization.
- Gray: no data.

Current thresholds:

- Green `< 40%`
- Yellow `40% - 70%`
- Red `>= 70%`

This mode is designed to identify congestion risk.

### 11.12 Load Balancing Analysis

`Load Balancing Analysis` checks whether multiple links between the same two devices are balanced.

The logic is:

1. Find multiple links between the same device pair.
2. Read utilization for each link using the current metric.
3. Calculate the difference between the maximum and minimum utilization.
4. Color the link group based on the difference.

Thresholds:

- Green `< 5%`
- Yellow `10% - 20%`
- Red `>= 20%`

The `5% - 10%` range is treated as an observation zone rather than a strong warning.

Link Congestion Analysis and Load Balancing Analysis are mutually exclusive because both use edge color to express different meanings. Enabling both at the same time would create ambiguous color semantics.

### 11.13 Utilization Filter

The utilization filter shows only links above a selected threshold.

Links below the threshold are hidden, and nodes without visible links are also hidden. This is a view filter and does not delete raw data.

### 11.14 Cache

Utilization results are cached to avoid repeated queries for the same device and interface.

The cache provides several benefits:

- Reduces Zabbix API load.
- Reduces CLI logins.
- Restores utilization display after page refresh.
- Reuses known bandwidth values instead of querying them repeatedly.

## 12. Context Menu Design

The context menu is grouped by function to avoid mixing all actions together.

Groups include:

- Edit: pin position, delete device, delete link.
- Path/group: set as transit node, group devices, expand group, rename group, return to main view, remove from group.
- Color: custom node color.
- Copy: copy name, copy address, copy link information.
- Query: current utilization, time-range utilization, append query.
- Create: add node, add Cloud, add link, add link description.

The menu position is automatically adjusted based on viewport boundaries. If the user right-clicks near the bottom of the page, the menu opens upward like a system context menu.

## 13. Export Capabilities

### 13.1 PNG Export

PNG export is used for quickly sharing the current topology view.

The exported image is based on the current visible canvas and is suitable for quick communication and reporting.

### 13.2 draw.io Export

draw.io export converts the current topology into an editable diagram.

The exported file preserves:

- Currently visible nodes.
- Currently visible links.
- Node positions.
- Link labels.
- Curved edges.

The user can open the file in draw.io and continue editing.

### 13.3 Mermaid Copy

Mermaid export is used for text-based topology diagrams.

The copied content is based on the current visible result. The tool does not build a separate customized topology for Mermaid; it exports what the page currently shows as closely as possible.

### 13.4 Link Summary CSV

Link summary CSV is used for audit, review, and capacity analysis.

If multi-link aggregation is enabled, exported fields include:

- Source node.
- Destination node.
- TX.
- RX.
- Bandwidth.

If multi-link aggregation is disabled, exported fields include:

- Source node + source port.
- Destination node + destination port.
- TX.
- RX.
- Bandwidth.

The export also includes query context:

- Data source.
- Current metric.
- Query mode.
- Time range.
- Export time.

If the data source is CLI, the file records `data_source=CLI`. If the data source is Zabbix, it records the Zabbix query conditions.

### 13.5 Json Export

Json export saves the complete workspace state.

It is not just a CSV export. It is a recoverable session containing:

- Data rows.
- Coordinates.
- Group information.
- Group-view coordinates.
- Deletion state.
- Custom colors.
- Link descriptions.
- Manual nodes and links.
- UI settings.

This is suitable for continuing work later or transferring the topology to another browser or machine.

## 14. State Persistence and Refresh Recovery

The page saves the current session state to avoid losing work after refresh.

Refresh recovery can include:

- Current imported data.
- Node coordinates.
- Deletion and filter state.
- Node groups.
- Manual nodes.
- Link descriptions.
- Utilization display.

When the user explicitly imports a new Json session, the old page session is cleared so that refresh does not restore an older topology by mistake.

## 15. Performance Design

### 15.1 Incremental Cytoscape Updates

Large graph performance depends heavily on avoiding full graph destruction after every operation.

The current implementation tries to:

- Reuse the existing Cytoscape instance.
- Add only new elements.
- Remove only missing elements.
- Update data on existing nodes and edges.
- Preserve selection state.
- Preserve viewport state.

This is more stable than calling `cy.destroy()` and rebuilding the whole graph, especially for graphs with thousands of links.

### 15.2 Large Graph Rendering Mode

When the edge count is large, the tool enables lighter rendering behavior:

- Lower pixel ratio.
- Simpler edge rendering during drag.
- Hide arrows and labels during drag.
- Use lighter edge curves for dense graphs.

The purpose is to keep node selection and dragging responsive even when the graph contains many labels and edges.

### 15.3 On-Demand Labels

Port labels, utilization labels, TX/RX, total bandwidth, and link descriptions are controlled by checkboxes.

More labels mean higher rendering cost in large graphs. Users can enable only the information needed for the current analysis task.

### 15.4 Collection-Side Optimization

CLI collection performance is optimized through:

- Ping precheck.
- Fast failure without retries.
- Pagination disablement before vendor detection.
- Short timeout for vendor detection.
- Short timeout for device-name detection.
- Parallel collection.
- Depth-limited recursion.
- Debug logs for slow-device identification.

These optimizations target data collection latency, while Cytoscape optimizations target rendering latency. Both are required for a smooth end-to-end workflow.

## 16. Reliability and Maintainability

### 16.1 Source Isolation

CLI, SQL, NDMP, CSV, and Json have different collection methods, but all normalize into standard LLDP rows. This decouples data collection from frontend topology generation.

### 16.2 Raw Data Is Not Easily Destroyed

Filtering, hiding, path trimming, and deleting devices generally do not delete raw data directly. They maintain view state or deletion stacks.

This allows users to explore and clean the view with lower risk.

### 16.3 Manual Edits Are Persistent

Manual nodes, Cloud nodes, manual links, link descriptions, custom colors, and node groups are included in Json export.

This makes the tool useful not only for one-time topology viewing, but also for maintaining an operational topology over time.

### 16.4 Debug Traceability

CLI and utilization queries provide debug output. When data is missing, the operator can distinguish whether the problem is:

- Login failure.
- Command returned no output.
- Parser failure.
- Device did not provide neighbor IP.
- Zabbix host match failure.
- Zabbix item missing.
- Bandwidth missing.
- No data in the selected time window.

This is more actionable than only showing `no data`.

## 17. Core Value Summary

The value of this tool is not only automatic topology drawing. It closes the loop across multiple network topology maintenance problems:

1. Multi-source collection: CLI, SQL, NDMP, CSV, and Json can all enter the same topology pipeline.
2. Automatic recursion: starting from one device, the tool expands topology through LLDP adjacency.
3. Data governance: device name/IP normalization, link deduplication, historical data isolation, and automatic field inference.
4. Visual layout: force-directed layout, hierarchy, partitioning, name-similarity clustering, and manual alignment.
5. Operational editing: delete/restore, undo/redo, manual nodes, manual links, and link descriptions.
6. Large-scale management: node groups, group view, isolated group-view coordinates, and group-level operations.
7. Performance analysis: CLI/Zabbix utilization, bandwidth detection, congestion analysis, and load balancing analysis.
8. Result reuse: PNG, draw.io, Mermaid, link summary CSV, and Json sessions.

The result is that engineers can move from “manual device login, command copying, spreadsheet cleanup, and manual diagram drawing” to “enter a start point, collect automatically, generate automatically, analyze as needed, and export reusable results.” This significantly reduces topology maintenance time and reduces errors caused by historical data, duplicate links, inconsistent device aliases, and manual diagramming.
