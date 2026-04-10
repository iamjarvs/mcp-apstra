# Apstra Reference Design Guide — LLM Interpretation Reference

This document is written to help a large language model (LLM) understand, interpret, and explain Juniper Apstra-rendered device configurations. It describes the architecture patterns, configuration building blocks, and inter-device relationships across the supported reference designs.

The configurations in this folder are real rendered JunOS configurations produced by Apstra. Values such as IP addresses, ASNs, VNI numbers, VLAN IDs, and hostnames are deployment-specific and will differ between environments. The **structural patterns** described here remain consistent across deployments.

---

## Table of Contents

1. [How to Read an Apstra JunOS Configuration](#1-how-to-read-an-apstra-junos-configuration)
2. [Common Building Blocks](#2-common-building-blocks)
3. [Reference Design: 3-Stage Clos Fabric](#3-reference-design-3-stage-clos-fabric)
4. [Reference Design: 5-Stage Clos Fabric](#4-reference-design-5-stage-clos-fabric)
5. [Reference Design: Collapsed Fabric](#5-reference-design-collapsed-fabric)
6. [Access Switches (All Designs)](#6-access-switches-all-designs)
7. [DCI — EVPN Over The Top (OTT)](#7-dci--evpn-over-the-top-ott)
8. [DCI — EVPN Stitching](#8-dci--evpn-stitching)
9. [Cross-Cutting Patterns and Policies](#9-cross-cutting-patterns-and-policies)
   - [9.1 BGP Community Architecture](#91-bgp-community-architecture)
   - [9.1a JunOS Route Policy Processing Model](#91a-junos-route-policy-processing-model)
   - [9.1b Common Routing Policy Failure Modes](#91b-common-routing-policy-failure-modes)
   - [9.1c Full Loop-Prevention Trace: 3-Stage Example](#91c-full-loop-prevention-trace-3-stage-example)
10. [Configuration Section Quick Reference](#10-configuration-section-quick-reference)

---

## 1. How to Read an Apstra JunOS Configuration

### The `replace:` Directive

Throughout all Apstra-rendered configurations you will see interface stanzas prefixed with `replace:`:

```
replace: ge-0/0/0 {
    description "facing_spine1:ge-0/0/5";
    ...
}
```

This is a JunOS configuration action keyword. When Apstra pushes configuration to a device, the `replace:` directive tells JunOS to completely replace that named stanza — removing any local configuration that was there before and substituting Apstra's version. This is how Apstra asserts full control over its managed configuration sections while leaving other parts of the device configuration untouched.

**What this means for interpretation:** Every `replace:` interface block is exactly what Apstra intends for that interface. Interfaces not yet wired or not assigned to a blueprint role are set to a bare `unit 0 { family inet; }` placeholder, which is the Apstra default for unassigned ports. You should treat these as "empty/unused" ports.

### Configuration Hierarchy

A JunOS configuration is structured as a hierarchy of stanzas. The top-level sections found in Apstra-managed configurations are:

| Section | Purpose |
|---|---|
| `system` | Hostname and global system settings |
| `chassis` | Hardware-level settings (LAG count, FPC/PIC config) |
| `interfaces` | Physical, logical, IRB, loopback, and LAG interface configuration |
| `forwarding-options` | VXLAN routing, EVPN forwarding options, ECMP load-balancing internal knobs |
| `routing-instances` | Named routing tables — VRFs (type `vrf`) and EVPN L2 domains (type `mac-vrf`) |
| `routing-options` | Global routing parameters — router-id, AS number, forwarding table export policy |
| `protocols` | Protocol-specific config: BGP, LLDP, RSTP, EVPN (top-level), L2-learning |
| `policy-options` | Route policy statements, community definitions, prefix-filter lists |
| `vlans` | (Sometimes inline within routing-instances) VLAN definitions with VXLAN bindings |

---

## 2. Common Building Blocks

These elements appear in every reference design.

### 2.1 Loopback Interface (`lo0`)

Every device has a loopback `lo0` with multiple logical units:

- `lo0.0` — The **underlay loopback**, used as the BGP router-id and as the VTEP source interface for VXLAN encapsulation. All EVPN tunnels terminate here.
- `lo0.2`, `lo0.3`, etc. — **VRF loopbacks**, one per L3 routing instance. These provide a stable per-VRF route advertisement source.

Example:
```
lo0 {
    unit 0 { family inet { address 172.16.0.1/32; } }   # underlay / VTEP
    unit 2 { family inet { address 172.16.0.4/32; } }   # Prod_VRF loopback
    unit 3 { family inet { address 172.16.0.7/32; } }   # Staging_VRF loopback
}
```

### 2.2 Fabric Links (Point-to-Point /31s)

Spine-facing and leaf-facing links always use IPv4 /31 addresses per RFC 3021. MTU is set to 9192 on the physical interface with `family inet mtu 9170` on the logical unit to accommodate VXLAN overhead (50 bytes) within a 9220-byte outer frame.

```
ge-0/0/0 {
    description "facing_spine1:ge-0/0/5";
    mtu 9192;
    unit 0 {
        family inet {
            mtu 9170;
            address 192.168.0.3/31;
        }
    }
}
```

### 2.3 IRB Interfaces (Integrated Routing and Bridging)

IRB interfaces are the L3 gateways for each VLAN/VNI on a leaf. Apstra programmes all leafs in the same fabric with the **same anycast MAC address** (`00:1c:73:00:00:01`) for each IRB unit. This is the EVPN anycast gateway pattern — every leaf appears to end hosts as the same default gateway MAC, regardless of which leaf the host is attached to.

```
irb {
    unit 101 {
        mac 00:1c:73:00:00:01;
        family inet {
            mtu 9000;
            address 10.80.101.1/24;
        }
    }
}
```

For the DCI stitching scenario, some IRBs use `virtual-gateway-address` instead of a simple address. This is an active-active IRB gateway mechanism for devices that support it when two border leafs peer directly and need a shared virtual IP:

```
unit 21 {
    virtual-gateway-v4-mac 00:1c:73:00:00:01;
    virtual-gateway-accept-data;
    family inet {
        mtu 9000;
        address 10.251.12.2/29 virtual-gateway-address 10.251.12.1;
    }
}
```

### 2.4 ESI LAG (Ethernet Segment Identifier — Link Aggregation)

Multi-homed servers or access switches connect to a pair of leafs via an LACP LAG. Apstra uses `all-active` ESI mode, meaning both leaf members in the LAG actively forward traffic (not active/standby). The ESI value is globally unique per multi-homed endpoint.

```
ae1 {
    description "to.esxi-h2";
    esi {
        00:02:00:00:00:00:01:00:00:01;
        all-active;
    }
    aggregated-ether-options {
        lacp {
            active;
            system-id 02:00:00:00:00:01;
        }
    }
```

The `system-id` in LACP must match on both leaf members so the connected server sees a single logical peer.

### 2.5 BGP Underlay (IP Fabric)

The underlay carries IPv4 unicast to build reachability between all loopbacks. It uses eBGP (external BGP) across every link — each device has a unique ASN (there is no iBGP in the data-plane fabric). The BGP group names follow the Apstra convention:

| Group name | Used on | Direction |
|---|---|---|
| `l3clos-l` | Leaf | Leaf → Spine (and Spine → Leaf from spine's perspective of the same session) |
| `l3clos-s` | Spine | Spine ↔ Leaf; also Spine ↔ SuperSpine in 5-stage |
| `l3clos-s-evpn` | Spine | EVPN overlay sessions to leafs; also to superspines in 5-stage |
| `l3clos-a` | Access | Access ↔ Leaf (l3clos-a used for access tier in collapsed fabric) |

BFD (Bidirectional Forwarding Detection) is always enabled on underlay BGP sessions for sub-second failure detection:
```
bfd-liveness-detection {
    minimum-interval 1000;
    multiplier 3;
}
```
3 missed intervals at 1-second = 3-second failure detection on underlay sessions.

### 2.6 BGP EVPN Overlay

The EVPN overlay carries MAC/IP and route type 5 (IP prefix) advertisements between all VTEP leafs. It runs as eBGP as well, but with `multihop` (TTL 1 or 2) because the EVPN sessions are established between **loopback addresses** (not the directly-connected link addresses).

```
group l3clos-l-evpn {
    type external;
    multihop {
        no-nexthop-change;
        ttl 1;
    }
    family evpn {
        signaling { loops 2; }
    }
    bfd-liveness-detection {
        minimum-interval 3000;
        multiplier 3;
    }
}
```

Key parameters:
- `no-nexthop-change` — The BGP next-hop is **not** changed as EVPN routes transit through the spine. Each leaf advertises its own loopback as the EVPN next-hop (VTEP address), and spines reflect this unchanged to other leafs.
- `loops 2` — EVPN routes may appear to loop through ASNs when they pass through the same AS on re-advertisement. The `loops 2` setting permits this.
- BFD interval is 3000ms on EVPN sessions (spines and leafs have higher tolerance for EVPN session flap than for fast underlay reroute).

### 2.7 The `mac-vrf` Routing Instance (`evpn-1`)

Every leaf has a `mac-vrf` routing instance (Apstra names it `evpn-1`) that acts as the EVPN L2 domain:

```
routing-instances {
    evpn-1 {
        instance-type mac-vrf;
        protocols {
            evpn {
                vni-options {
                    vni 10000 { vrf-target target:10000:1; }
                    ...
                }
                encapsulation vxlan;
                default-gateway do-not-advertise;
                extended-vni-list all;
            }
        }
        vtep-source-interface lo0.0;
        service-type vlan-aware;
        route-distinguisher <loopback>:65534;
        vrf-target target:100:100;
        interface <server-facing-ports>;
        vlans {
            vn101 {
                vlan-id 101;
                description "Web_Prod";
                vxlan { vni 10000; }
                l3-interface irb.101;
            }
        }
    }
}
```

Key concepts:
- **`vlan-aware` service type** — One mac-vrf instance handles all VLANs. Each VLAN maps to a unique VNI.
- **`vrf-target target:100:100`** — The "all fabric" EVPN route target. All leafs in the same fabric import/export this target, which is how all VTEP leafs know about all MAC/IP entries across the fabric.
- **VNI per VLAN** — Each `vlans {}` entry maps a local VLAN ID to a VXLAN Network Identifier (VNI). If a VLAN-to-VNI mapping is 1:1 with the same number, it may also be called a "symmetric" VNI mapping.
- **`l3-interface irb.X`** — Links the L2 bridging domain (the VLAN) to its L3 gateway (the IRB unit). This is how inter-VLAN routing is achieved at the leaf.
- **`default-gateway do-not-advertise`** — The anycast gateway MAC is not flooded as a default gateway advertisement into the EVPN network; hosts learn the gateway via normal ARP.
- **`extended-vni-list all`** — All configured VNIs participate in EVPN.

### 2.8 L3 VRF Routing Instances

Leafs host one or more VRF (`vrf` type) routing instances for tenant L3 routing. Each VRF corresponds to a traffic isolation boundary (e.g., Prod vs Staging):

```
Prod_VRF {
    instance-type vrf;
    interface irb.101;
    interface irb.103;
    interface lo0.2;
    route-distinguisher 172.16.0.1:2;
    vrf-target target:20000:1;
    routing-options { multipath; auto-export; graceful-restart; }
    protocols {
        evpn {
            ip-prefix-routes {
                advertise direct-nexthop;
                encapsulation vxlan;
                vni 20000;
                export BGP-AOS-Policy-Prod_VRF;
            }
        }
    }
}
```

- **`route-distinguisher <loopback>:<unique-id>`** — Makes this VRF's routes globally unique in the BGP EVPN table. The loopback address ensures per-device uniqueness.
- **`vrf-target target:20000:1`** — Route target used to import/export VRF routes across leafs. All leafs with the same VRF use the same RT.
- **`ip-prefix-routes advertise direct-nexthop`** — Advertises this VRF's connected (IRB) subnets into EVPN as Type 5 (IP Prefix) routes with the leaf's own loopback as the next-hop.
- **`irb-symmetric-routing`** (seen in stitching/DCI designs) — Enables symmetric IRB mode where the VNI is used bidirectionally. Without this the default is "asymmetric" routing.

### 2.9 Route Policy Framework

Apstra generates a consistent route policy framework. Key policy statements:

| Policy | Role |
|---|---|
| `AllPodNetworks` | Matches directly-connected (loopback/IRB) routes |
| `BGP-AOS-Policy` | Accept directs + BGP-learned routes; used as underlay export |
| `BGP-AOS-Policy-<VRF>` | Same for per-VRF routes |
| `LEAF_TO_SPINE_FABRIC_OUT` | Leaf underlay export: reject routes tagged as coming from spine tier (prevent re-advertisement back to spine); accept all others |
| `SPINE_TO_LEAF_FABRIC_OUT` | Spine underlay export: tag routes with `FROM_SPINE_FABRIC_TIER` community (0:15) |
| `LEAF_TO_SPINE_EVPN_OUT` | Leaf EVPN export: reject routes already tagged from spine tier; accept others |
| `SPINE_TO_LEAF_EVPN_OUT` | Spine EVPN export: tag routes with `FROM_SPINE_EVPN_TIER` community (0:14) |
| `EVPN_EXPORT` | Accept all EVPN routes (used as leaf catch-all for EVPN sessions) |
| `PFE-LB` | `load-balance per-packet` — enables ECMP across equal-cost paths |

The community-based loop prevention works as follows: when a spine sends a route to a leaf it tags it with `0:15` (fabric tier) or `0:14` (EVPN tier). The leaf's export policy to the spine checks for the presence of this community and explicitly rejects routes carrying it. This prevents a leaf from re-advertising a spine route **back** to a spine, which would create a loop.

---

## 3. Reference Design: 3-Stage Clos Fabric

### Architecture Overview

```
      ┌───────────┐     ┌───────────┐
      │  Spine 1  │     │  Spine 2  │
      │ AS 64512  │     │ AS 64513  │
      └─────┬─────┘     └─────┬─────┘
            │ eBGP/EVPN overlay │
     ┌──────┴──────────────────┴──────┐
     │       │             │          │
┌────┴────┐  │        ┌────┴────┐  ...
│ Leaf 1  │  │        │ Leaf 2  │
│ AS 64514│  │        │ AS 64515│
└────┬────┘  │        └────┬────┘
     │       │             │
  Servers   ...          Servers
```

A 3-stage Clos fabric consists of exactly two tiers: **Spines** and **Leafs**. Every leaf connects to every spine; there are no leaf-to-leaf links (except ESI LAG partner links to support multi-homing, which are transparent to the network layer). This results in equal-cost multi-path (ECMP) between any leaf pair via any spine.

### Spine Configuration

The spine in a 3-stage fabric is a **pure routing device with no L2 or tenant services**:

- **No IRB interfaces** — Spines do not host VLANs or VRFs.
- **No `routing-instances`** — No VRF or mac-vrf instances. All routing is in the global (default) table.
- **RSTP disabled** (`replace: rstp { disable; }`) — Spanning Tree is not needed on a pure L3 spine.
- **Loopback** (`lo0.0`) is the only loopback unit; no VRF loopbacks are needed.
- **BGP Role:** The spine is a **BGP route reflector for EVPN** (even though it uses eBGP — the spine reflects EVPN routes between leafs because the leafs build their EVPN sessions *to the spine loopback*, not directly to each other).

The spine's BGP configuration:
```
group l3clos-s {          # Underlay — IPv4 unicast to/from all leafs
    type external;
    neighbor 192.168.0.1 { description "facing_leaf1"; peer-as 64514; ... }
    neighbor 192.168.0.3 { description "facing_leaf2"; peer-as 64515; ... }
    neighbor 192.168.0.5 { description "facing_leaf3"; peer-as 64516; ... }
}

group l3clos-s-evpn {     # EVPN overlay — multihop to leaf loopbacks
    type external;
    multihop { no-nexthop-change; ttl 1; }
    family evpn { signaling { loops 2; } }
    neighbor 172.16.0.0 { description "facing_leaf1-evpn-overlay"; peer-as 64514; ... }
    neighbor 172.16.0.1 { description "facing_leaf2-evpn-overlay"; peer-as 64515; ... }
    neighbor 172.16.0.2 { description "facing_leaf3-evpn-overlay"; peer-as 64516; ... }
}
```

The spine exports a `SPINE_TO_LEAF_FABRIC_OUT` community tag (`FROM_SPINE_FABRIC_TIER = 0:15`) on underlay routes and `SPINE_TO_LEAF_EVPN_OUT` tag (`FROM_SPINE_EVPN_TIER = 0:14`) on EVPN routes. These community tags are the anti-loop mechanisms.

### Leaf Configuration

The leaf is the full-service switch that connects to end hosts and provides all EVPN/VXLAN encapsulation:

**Connectivity:**
- **Fabric uplinks** (ge-0/0/0, ge-0/0/1 — numbered interfaces) point to each spine with /31 IPs.
- **Server-facing ports** (ge-0/0/2 through ge-0/0/4 etc.) in `family ethernet-switching trunk` mode carrying multiple VLANs.
- **ESI LAG** (ae1) for multi-homed servers — LACP active, `all-active` ESI.

**Routing instances on the leaf:**
1. **`evpn-1`** (mac-vrf) — Bridges all VLANs. Each VLAN maps to a VNI. IRB interfaces are linked per VLAN.
2. **`Prod_VRF`** (vrf) — All "production" VLANs' IRBs are members. Routes are distributed via EVPN Type 5.
3. **`Staging_VRF`** (vrf) — All "staging" VLANs' IRBs are members.
4. Additional VRFs as needed by the design.

**Key BGP groups on the leaf:**
```
group l3clos-l {              # Underlay IPv4 unicast to spines
    type external;
    neighbor 192.168.0.2 { peer-as 64512; export (LEAF_TO_SPINE_FABRIC_OUT && BGP-AOS-Policy); }
    neighbor 192.168.0.8 { peer-as 64513; export (LEAF_TO_SPINE_FABRIC_OUT && BGP-AOS-Policy); }
}

group l3clos-l-evpn {         # EVPN overlay to spine loopbacks
    type external;
    multihop { no-nexthop-change; ttl 1; }
    neighbor 172.16.0.9 { peer-as 64512; export (LEAF_TO_SPINE_EVPN_OUT && EVPN_EXPORT); }
    neighbor 172.16.0.10 { peer-as 64513; export (LEAF_TO_SPINE_EVPN_OUT && EVPN_EXPORT); }
}
```

The leaf's `LEAF_TO_SPINE_FABRIC_OUT` policy rejects routes already tagged with `FROM_SPINE_FABRIC_TIER (0:15)` to prevent looping spine-learned routes back to spines. Routes not bearing this community are accepted and forwarded.

**RSTP on the leaf:**
Spanned tree is enabled but with `bridge-priority 0` (highest priority, making the leaf root for STP) and `bpdu-block-on-edge` on server-facing ports. This prevents broadcast storms from edge ports while keeping STP functional for the leaf's ESI LAG links.

### Inter-Device Data Flow Summary (3-Stage)

1. **MAC/IP learning**: A new host (e.g., on vn101, VNI 10000) ARPs on Leaf2's server port → Leaf2 learns the MAC/IP locally → Leaf2 advertises EVPN Type 2 (MAC/IP) route to Spine1 and Spine2 via `l3clos-l-evpn` → Spines reflect the Type 2 route to Leaf1 and Leaf3 → All leafs install a VXLAN forwarding entry: "MAC X is reachable via Leaf2's loopback".

2. **L3 routing**: Traffic from vn101 (Prod_VRF) to vn102 (Staging_VRF) is inter-VRF. On the originating leaf the IRB routes between VRFs locally if both VRFs are present. For traffic to an address on a VRF hosted only on another leaf, the Prod_VRF on Leaf2 has an EVPN Type 5 route pointing to Leaf3's loopback as next-hop. VXLAN encapsulation carries the original IP packet.

3. **ECMP**: `forwarding-table export PFE-LB` with `ecmp-fast-reroute` enables traffic to be sprayed across paths to both Spine1 and Spine2 simultaneously.

---

## 4. Reference Design: 5-Stage Clos Fabric

### Architecture Overview

```
         ┌──────────────────────────────────────┐
         │         SuperSpine Tier               │
         │  superspine-qfx5700-01 AS 4200000100  │
         │  superspine-qfx5700-02 AS 4200000100  │
         └────────────────┬─────────────────────┘
                          │ (multiple pods connect here)
         ┌────────────────┴───────────────────────────────────┐
         │ Pod: Compute    │ Pod: Storage     │ Pod: Service   │
         │                 │                  │                │
   ┌─────┴──────┐    ┌─────┴──────┐   ┌──────┴──────┐
   │compute-    │    │storage-    │   │service-pod- │
   │spine-ptx-01│    │spine-ptx-01│   │spine-01     │
   │AS 4200000102    │AS 4200000101│   │AS 4200000103│
   └─────┬──────┘    └─────┬──────┘   └──────┬──────┘
         │                 │                  │
   ┌─────┴──────┐    ┌─────┴──────┐   ┌──────┴──────┐
   │comp-leaf-  │    │storage-    │   │border-leaf  │
   │qfx5110-01  │    │leaf-XX     │   │(sp-bl-01)   │
   │AS 4200000106    │...         │   │AS 4200000108│
   └────────────┘    └────────────┘   └─────────────┘
```

The 5-stage Clos adds a **Super-Spine tier** above the pod spines. This enables the fabric to scale to multiple independent pods (compute, storage, service) while maintaining full-mesh reachability. Real deployments label it "5-stage" because the path from one leaf to another crosses 5 devices: Leaf → Pod-Spine → Super-Spine → Pod-Spine → Leaf.

### Super-Spine Configuration

The super-spine is analogous to the spine in a 3-stage fabric but connects to pod-level spines rather than leafs:

- **No L2, no VRFs, no IRBs** — Pure IP routing device.
- **RSTP disabled** — Not needed.
- Uses 40G links (`et-0/0/x` interfaces with `speed 40g`) to connect to pod spines.
- **Note:** The super-spine here uses AS `4200000100` for **both** super-spine nodes. This is because the super-spine pair act as a single logical tier and both peer with the same pod spines using the same AS. This is allowed in JunOS eBGP where `multiple-as` multipath is configured.
- Policies: `SUPERSPINE_TO_SPINE_FABRIC_OUT` (tags with `FROM_SUPERSPINE_FABRIC_TIER = 0:13`) and `SUPERSPINE_TO_SPINE_EVPN_OUT` (tags with `FROM_SUPERSPINE_EVPN_TIER = 0:12`).

### Pod-Spine Configuration

The pod-spine has **four BGP groups** (compared to two in the 3-stage spine):

```
group l3clos-s {
    # Underlay to BOTH leafs (downward) AND superspines (upward)
    neighbor 10.1.0.29 { description "facing_comp-leaf-qfx5110-01"; peer-as 4200000106; 
        export (SPINE_TO_LEAF_FABRIC_OUT && BGP-AOS-Policy); }
    neighbor 10.1.0.9  { description "facing_superspine-qfx5700-01"; peer-as 4200000100;
        export (SPINE_TO_SUPERSPINE_FABRIC_OUT && BGP-AOS-Policy); }
    ...
}

group l3clos-s-evpn {
    # EVPN overlay to BOTH leafs AND superspines
    neighbor 10.11.0.9  { description "facing_comp-leaf-qfx5110-01-evpn-overlay"; ...
        export (SPINE_TO_LEAF_EVPN_OUT); }
    neighbor 10.11.0.0  { description "facing_superspine-qfx5700-01-evpn-overlay"; ...
        export (SPINE_TO_SUPERSPINE_EVPN_OUT); }
    ...
}
```

Two **additional community tiers** for the upward direction:
- `SPINE_TO_SUPERSPINE_FABRIC_OUT` — Tags routes going up to super-spine with `FROM_SPINE_FABRIC_TIER (0:15)` but also has a term that rejects routes already tagged with `FROM_SUPERSPINE_FABRIC_TIER (0:13)` to prevent re-advertising super-spine routes back up.
- `SPINE_TO_SUPERSPINE_EVPN_OUT` — Same pattern for EVPN tier.

### 5-Stage Leaf Configuration

The leaf is functionally identical to the 3-stage leaf. The only difference is:
- It connects to **two pod-spines** (not two fabric-wide spines) via 40G `et-0/0/48` and `et-0/0/49` links.
- The border leaf (`sp-bl-01`) is a leaf in the "service pod" that connects to the service-pod-spine and additionally hosts DCI and external connectivity (covered in the DCI stitching section below).

The VRF instances, mac-vrf, EVPN, and IRB configuration are structurally identical to 3-stage leafs.

### Why 5-Stage vs 3-Stage?

A 3-stage fabric places all leafs in a single flat tier connected to the same spines. As the fabric grows (more leaf switches, more VNIs, more servers), the spine must maintain BGP sessions to every leaf, and the EVPN control-plane load on spine grows linearly with the leaf count. The 5-stage design delegates control-plane load to pod-level spines: the super-spine only peers with pod-spines (a small number), and each pod-spine only peers with the leafs in its pod.

---

## 5. Reference Design: Collapsed Fabric

### Architecture Overview

```
   WAN-Router-1          WAN-Router-2
        │       (BGP/VRF)       │
        └─────────┬─────────────┘
                  │
    ┌─────────────┴──────────────┐
    │    cf-rack-001-leaf1        │◄──── peer link (direct L3 /31)
    │   (Collapsed Spine+Leaf)    │                   │
    │    AS 4200000300            │    cf-rack-001-leaf2
    └─────────────┬───────────────┘    AS 4200000301
                  │ (EVPN gateway peering to DC1/DC2)
          ┌───────┴───────┐
          │  access1      │ ae1 (ESI to leaf pair)
          │  access2      │
          └───────────────┘
```

The **Collapsed Fabric** is a design where the spine and leaf roles are **combined into a single pair of switches**. This is suitable for smaller deployments or remote sites where a full 3-tier hierarchy would be over-engineered. Each collapsed spine-leaf switch:

1. Acts as a **leaf** — hosts VLANs, VRFs, IRBs, EVPN VXLAN.
2. Acts as a **spine** — peers with access switches below it.
3. Acts as a **border leaf** — peers directly with WAN routers above it.

### Collapsed Spine-Leaf Configuration

**Connectivity:**
- **WAN-facing** (et-0/0/2, et-0/0/3 with `flexible-vlan-tagging`): Multiple VRF sub-interfaces on physical WAN links. VLAN IDs are used to separate VRF BGP sessions on the same physical link to the WAN router. For example:
  - `et-0/0/2.0` (vlan-id 1) = Default VRF
  - `et-0/0/2.5` (vlan-id 5) = DC3-APP5 VRF
  - `et-0/0/2.6` (vlan-id 6) = DC3-APP6 VRF
- **Peer link** (et-0/0/4, et-0/0/5): Direct L3 routed links to the peer collapsed leaf (cf-rack-001-leaf2). These are regular /31 point-to-point links used for leaf-to-leaf BGP (`l3clos-l` group).
- **Access downlinks** (ae1): ESI LAG to the access switch pair.

**BGP Groups Present:**
1. `l3clos-l` — Underlay peering to the peer collapsed leaf (no spine exists separately).
2. `l3clos-l-evpn` — EVPN overlay to the peer collapsed leaf loopback.
3. `l3rtr` — External BGP to WAN routers (per-VRF sessions with import/export policies).
4. `evpn-gw` — EVPN gateway sessions to remote data centre border leafs for cross-DC connectivity.

**VRF Instances:**
- Per-application VRFs (DC3-APP5, DC3-APP6) with WAN connectivity via the `l3rtr` BGP group.
- **L2-DCI VRF** — Provides an L3 anchoring point for L2-stretched VLANs (using IRB with `irb-symmetric-routing`).

**Cross-DC EVPN Gateway Peering:**
The collapsed fabric leaf can act as an EVPN gateway to peer data centres. It uses the `evpn-gw` BGP group to establish eBGP EVPN sessions (long multihop TTL 30) directly to border leafs in DC1 and DC2. This allows VNIs to be stretched across DCs. The `EVPN_GW_IN/OUT` policies control which EVPN routes cross the DCI boundary.

**`forwarding-options`** block on collapsed fabric leafs:
```
forwarding-options {
    vxlan-routing {
        next-hop 45056;
        interface-num 8192;
        overlay-ecmp;
    }
    evpn-vxlan {
        shared-tunnels;
        vxlan-trans-vni-enable;      # Enable VNI translation feature
    }
}
```
`vxlan-trans-vni-enable` enables VNI translation which is required when the local fabric VNI space differs from a remote DC's VNI space (used in DCI stitching).

---

## 6. Access Switches (All Designs)

### Architecture Overview

Access switches sit downstream of a leaf pair and provide connectivity to end hosts (servers, compute nodes). They are used when a single rack has more servers than a leaf's available downlink ports, or to provide a dedicated access tier for specific host types.

Access switches can be attached to **any design** — 3-stage, 5-stage, or collapsed fabric. The access switch configuration and behaviour are identical regardless of which fabric type it connects to.

### Access Switch Configuration

**Topology role:**
- Uplinks to a **pair of leaf switches** via ESI LAG (`ae1` with all-active ESI). Both uplink members belong to the same ESI — the leaf pair presents as a single logical multi-chassis LAG endpoint.
- **Server downlinks** via another ESI LAG (`ae2`) to provide multi-homed server connectivity.
- **Peer link to access2** (`ae3`) — a regular L3 routed link for BGP between the two access switches in the pair.

**BGP:**
```
group l3clos-a {              # Underlay to peer access switch only
    type external;
    neighbor 10.3.0.4 {
        description "facing_cf-rack-001-access2";
        peer-as 4200000303;
        family inet { unicast; }
    }
}

group l3clos-a-evpn {         # EVPN overlay to peer access switch loopback
    type external;
    multihop { no-nexthop-change; ttl 1; }
    family evpn { signaling { loops 2; } }
    neighbor 10.13.0.3 {
        description "facing_cf-rack-001-access2-evpn-overlay";
        peer-as 4200000303;
    }
}
```

Notice that the access switch **does NOT peer BGP directly to the leaf switches** — it only peers to its access switch partner. The access switch relies on the ESI LAG to reach the leaf tier. BGP is only needed between the two access switches to synchronise MAC/IP table entries (EVPN Type 2 routes) between them, so that if one access-to-leaf uplink fails, the other access switch can still forward the traffic.

**EVPN on access:**
- `mac-vrf evpn-1` with VLANs mapped to VNIs — same pattern as leaf.
- `forwarding-options evpn-vxlan { shared-tunnels; }` — Enables VXLAN tunnel sharing between the two access switches (they can re-use the same VXLAN tunnel object for the ESI peer).
- `protocols evpn { no-core-isolation; }` — Disables core isolation on the access switch. Core isolation is a safety feature that stops the access switch from forwarding traffic if it loses all fabric uplinks. In multihomed access designs, isolation is handled by the ESI LAG mechanism instead, so this is disabled to prevent false traffic black-holing when only one uplink is down.

**Policy options on access switches** are simpler than on leafs — there is no fabric tier community tagging because the access switch does not participate in the multi-tier community loop prevention scheme. The `BGP-AOS-Policy` simply accepts direct routes.

---

## 7. DCI — EVPN Over The Top (OTT)

### Concept

EVPN Over The Top (OTT) is a Data Centre Interconnect method where EVPN-based VXLAN tunnels are extended across an existing IP network (the "transport" or "underlay") between two data centres. The border leaf in DC1 establishes a direct EVPN BGP session with the border leaf in DC2 across the IP transport — it does **not** require any additional overlay or transport setup; the IP network between sites is the tunnel underlay.

This is called "Over The Top" because the EVPN tunnels simply ride on top of whatever IP routed path exists between DCs.

### Border Leaf Configuration (OTT)

The OTT border leaf has **all the capabilities of a normal fabric leaf plus additional DCI connectivity**:

**DCI-specific interfaces:**
```
ge-0/0/2 {
    description "to.dc2-leaf6";
    flexible-vlan-tagging;
    native-vlan-id 1;
    unit 50 {
        description "VRF default to DC2-Leaf6";
        vlan-id 50;
        family inet { address 172.16.100.1/24; }
    }
}
ge-0/0/3 {               # Second link for redundancy
    unit 50 { address 172.16.200.1/24; }
}
```
These are routed sub-interfaces (flexible VLAN tagging) to the remote DC2 border leaf. They provide the IP path over which both L3 DCI and EVPN OTT sessions are built.

**Four BGP groups (vs two on a normal leaf):**

1. **`l3clos-l`** — Normal fabric underlay to both spines (unchanged from a regular leaf).
2. **`l3clos-l-evpn`** — Normal EVPN overlay to spine loopbacks (unchanged from a regular leaf).
3. **`l3rtr`** — **L3 routing to/from the remote DC** via the DCI interfaces. This is IPv4 unicast, used for inter-DC L3 routing in the default VRF. Import/export policies (`RoutesFromExt-default-DCI` / `RoutesToExt-default-DCI`) tightly control which subnets cross the DCI. Outbound routes have fabric communities stripped (`community delete FABRIC_COMMUNITIES`) to avoid leaking internal fabric community tags to the remote DC.
4. **`evpn-gw`** — **EVPN gateway peering to remote DC** for L2/L3 extension:
   ```
   group evpn-gw {
       type external;
       multihop { no-nexthop-change; ttl 30; }  # Long TTL for WAN
       vpn-apply-export;
       neighbor 172.16.0.18 {
           description "facing_to-dc2-evpn-gateway";
           peer-as 64519;
           family evpn { signaling; }
           import (EVPN_GW_IN);
           export (EVPN_GW_OUT && EVPN_EXPORT);
       }
   }
   ```
   The `ttl 30` is crucial — unlike intra-fabric EVPN sessions that use TTL 1 (direct neighbour), the remote DC EVPN partner is multiple IP hops away, so TTL is set high enough to traverse the WAN.

**OTT import/export policy:**
- `EVPN_GW_IN`: Tags inbound EVPN routes from the remote DC with the `EVPN_GW_IN` community. This distinguishes them from local fabric routes.
- `EVPN_GW_OUT`: Before sending EVPN routes outbound to the remote DC, deletes all `FABRIC_COMMUNITIES` (the tier-based community tags used for internal loop prevention). This prevents DC1's internal community tags from being interpreted by DC2's route policies.

### What Traffic Uses OTT?

- **L2 stretched VLANs**: A VLAN in DC1 can extend to DC2 via EVPN Type 2 (MAC/IP) routes. Hosts in both DCs appear on the same IP subnet.
- **L3 VRF stretching**: A VRF in DC1 can be extended to DC2 via EVPN Type 5 (IP prefix) routes. VMs in either DC reach each other via VRF routing without traversing a firewall.
- **Per the `l3rtr` group**: Plain IP routing between default-VRF subnets of the two DCs (no EVPN -- just regular BGP IPv4, useful for management or inter-DC services that don't need L2 extension).

---

## 8. DCI — EVPN Stitching

### Concept

EVPN Stitching (also called EVPN Multi-Domain or EVPN Interconnect) is the more sophisticated DCI method. Rather than stretching a single EVPN domain across multiple data centres (as in OTT), stitching **connects multiple independent EVPN domains** together while keeping their control planes separate. Each DC maintains its own EVPN domain with its own VNI space; the border leaf translates VNIs and re-originates EVPN routes when crossing the domain boundary.

Stitching is preferred when:
- DC1 and DC2 have independently managed EVPN domains.
- VNI numbering conflicts between DCs.
- You want to control exactly which L2 segments are stretched (only named VLANs in `interconnected-vni-list`).

### Border Leaf Configuration (Stitching)

The stitching border leaf is also an example of a **firewall-chained border leaf** — the SRX firewall cluster is connected via ESI LAG and each VRF peers to the firewall for traffic inspection.

**Key interfaces:**
- `et-0/0/24` — Uplink to `service-pod-spine-01` (fabric connectivity).
- `ae1`, `ae2` — Two ESI LAGs to **SRX4600 chassis cluster** (firewall). Each LAG connects to one node of the SRX cluster, providing redundant ESI-active/active firewall attachment. Traffic destined for any VRF is inspected by the SRX before forwarding into the fabric.
- `ae3` — ESI LAG to `sp-esxi-01` (ESXi server).
- `et-0/0/33`, `et-0/0/34` — WAN connections with `flexible-vlan-tagging`. Each VRF has its own sub-interface (e.g., INTERNET VRF uses vlan-id 4054, L3-DCI VRF uses vlan-id 8, default VRF uses vlan-id 1 / unit 0).

**Multiple loopback units:**
```
lo0 {
    unit 0  { address 10.11.0.11/32; }   # Underlay/VTEP
    unit 2  { address 10.11.0.15/32; }   # 1BLUE VRF
    unit 3  { address 10.11.0.31/32; }   # 1RED VRF
    unit 4  { address 10.11.0.57/32; }   # APP1 VRF
    unit 5  { address 10.11.0.59/32; }   # APP2 VRF
    ...
}
```

**VRF Instances and Firewall Chaining:**

The border leaf hosts many VRFs. Each security-sensitive VRF (1BLUE, 1RED, APP1, APP2, DB, INTERNET, WEB, Transit, L3-DCI) has an IRB unit that connects to the SRX via the ae1/ae2 ESI LAG. The VRF BGP group `l3rtr` peers to the SRX for that VRF:

```
1BLUE {
    instance-type vrf;
    interface irb.14;     # VRF subnets in the fabric
    interface irb.15;
    interface irb.21;     # Transit link to SRX for this VRF (vn21 vlan)
    interface lo0.2;
    protocols {
        bgp {
            group l3rtr {
                neighbor 10.251.12.4 {    # SRX VTEP address for 1BLUE VRF
                    local-address 10.251.12.2;
                    peer-as 4291111111;
                }
            }
        }
        evpn {
            interconnect {
                vrf-target target:65534:310;
                route-distinguisher 10.11.0.11:65521;
            }
            ip-prefix-routes { vni 310300; ... }
        }
    }
}
```

The `evpn { interconnect { ... } }` block is the **EVPN Interconnect** feature. It designates this VRF as participating in EVPN multi-domain stitching. Routes from the remote DC arriving via the `evpn-gw` group are stitched into this VRF.

**`evpn-1` mac-vrf with Interconnect and VNI Translation:**

```
routing-instances {
    evpn-1 {
        instance-type mac-vrf;
        protocols {
            evpn {
                interconnect {
                    vrf-target target:100:65123;
                    route-distinguisher 10.11.0.11:65533;
                    esi {
                        00:01:ff:00:00:00:01:00:00:01;
                        all-active;
                    }
                    interconnected-vni-list [ 300 301 310 311 7777 ];
                }
                ...
            }
        }
        vlans {
            vn14 {
                vlan-id 14;
                description "1BLUE-VLAN310";
                vxlan {
                    vni 310310;
                    translation-vni 310;  # ← VNI Translation
                }
                l3-interface irb.14;
            }
```

Key elements:
- **`interconnect { ... }`** within the mac-vrf defines the **inter-domain EVPN bridge**. The `vrf-target target:100:65123` is the EVPN route target used between the two DC domains (not the intra-fabric target `100:100`).
- **`interconnected-vni-list`** — Only VNIs listed here are allowed to cross the DCI boundary. VNI 300, 301, 310, 311, and 7777 map to specific VLANs in each DC domain.
- **`translation-vni`** — The internal fabric's VLAN 14 uses VNI `310310` within DC1's fabric. But the remote DC uses VNI `310` for the same L2 segment. The `translation-vni 310` tells JunOS to advertise this segment with VNI `310` to the remote DC (the external VNI) while using `310310` internally. This VNI space translation is the defining feature of stitching vs OTT.
- **ESI on the interconnect** (`00:01:ff:...`) — The two local border leafs (sp-bl-01 and its redundant partner) present a shared ESI to the remote DC, enabling equal-cost load-balancing from the remote DC perspective.

**`protocols evpn { interconnect-multihoming-peer-gateways [...]; }`** — Lists the loopback addresses of the local border leaf pair. Both leafs use this to coordinate EVPN state when one border leaf fails, ensuring MAC/IP entries are still reachable via the surviving leaf.

**BGP evpn-gw group:**
```
group evpn-gw {
    type external;
    multihop { no-nexthop-change; ttl 30; }
    neighbor 10.12.0.4 {
        description "facing_dc2-bl1-evpn-gateway";
        peer-as 4200000204;
        family evpn { signaling; }
        import (EVPN_GW_IN && EVPN_IMPORT);
        export (EVPN_GW_OUT && EVPN_EXPORT);
    }
    neighbor 10.13.0.0 { ... }   # DC3 border leaf 1
    neighbor 10.13.0.1 { ... }   # DC3 border leaf 2
}
```
The border leaf peers with multiple remote DC border leafs simultaneously. This makes the stitching leaf a "hub" for multi-DC connectivity.

**EVPN_GW_IN/OUT policies for stitching (stricter than OTT):**
```
policy-statement EVPN_GW_IN {
    term EVPN_GW_IN-10 {
        from { family evpn; community EVPN_DCI_L2_TARGET; }
        then { community add EVPN_GW_IN; community add NO_ADVERTISE; accept; }
    }
}
```
Only EVPN routes bearing the specific DCI L2 target community (`target:100:65123`) are accepted — this prevents accidentally importing all EVPN routes from the remote DC. The `NO_ADVERTISE` community ensures accepted DCI routes are not re-advertised further into the local fabric beyond the stitching layer.

---

## 9. Cross-Cutting Patterns and Policies

### 9.1 BGP Community Architecture

Apstra uses a hierarchical community-tagging scheme to prevent routing loops across the fabric tiers:

| Community | Value | Populated By | Used By |
|---|---|---|---|
| `FROM_SUPERSPINE_FABRIC_TIER` | 0:13 | SuperSpine | Spines reject routes with this tag going back to superspine |
| `FROM_SUPERSPINE_EVPN_TIER` | 0:12 | SuperSpine | Spines reject EVPN routes going back to superspine |
| `FROM_SPINE_FABRIC_TIER` | 0:15 | Spine | Leafs reject routes with this tag going back to spine |
| `FROM_SPINE_EVPN_TIER` | 0:14 | Spine | Leafs reject EVPN routes going back to spine |
| `DEFAULT_DIRECT_V4` | varies (e.g. 4:20007) | Leaf | Identifies directly-connected routes; used in export filters |
| `FABRIC_COMMUNITIES` | multiple | all tiers | Deleted when exporting routes outside the fabric (DCI) |
| `EVPN_GW_IN` | varies | Border Leaf | Marks EVPN routes received from DCI gateway |
| `EVPN_GW_OUT` | varies | Border Leaf | Identifies routes that came via DCI; prevents re-export |
| `RoutesFromExt-<VRF>` | varies | Border Leaf | Identifies routes received from external (WAN/DCI) for a VRF |

The `DEFAULT_DIRECT_V4` community value format is `<node-index>:20007` where `node-index` is an Apstra-assigned integer unique to each node in the fabric. This ensures a direct route from Leaf1 is distinguishable from the same prefix direct route on Leaf2 even within the same VRF.

#### Community Value Format

BGP communities in the standard format are `AA:NN` where both AA and NN are 16-bit integers (0–65535). Apstra uses several patterns:

- **`0:12`, `0:13`, `0:14`, `0:15`** — The first octet `0` is a well-known namespace for Apstra fabric tier markers. The second octet encodes the tier level.
- **`target:NNNN:1`** — Extended community in RT (Route Target) format. Used by VRFs to control route import/export. A route is imported into a VRF if its attached RT matches the VRF's `vrf-target import` value.
- **`target:NNNNL:1`** — The `L` suffix indicates a 6-byte (48-bit) extended community number (`large`-format route distinguisher). Apstra uses these for VNI-aligned RTs (e.g., `target:300000L:1` for VNI 300000).
- **`4:20007` style** — Used for `DEFAULT_DIRECT_V4` and `RoutesFromExt` communities. The second value `20007` is a fixed Apstra marker. The first octet distinguishes which node or policy assigned it.
- **`FABRIC_COMMUNITIES`** is a community list (not a single value) defined as a regex in policy: `members [ 0:12 0:13 0:14 0:15 .+:200.. 2....:260.. ]`. The regex patterns match any community value in those ranges. When a route exits the fabric to a DCI peer, all communities matching this list are stripped to avoid polluting the remote DC's policy processing.

---

### 9.1a JunOS Route Policy Processing Model

Understanding how JunOS evaluates route policies is essential for diagnosing why a route is or is not being advertised or accepted.

#### Policy Chains and the `&&` Operator

BGP export and import in Apstra configurations use **chained policies** with the `&&` operator:

```
export ( LEAF_TO_SPINE_FABRIC_OUT && BGP-AOS-Policy );
```

This means: evaluate `LEAF_TO_SPINE_FABRIC_OUT` first, then `BGP-AOS-Policy`. The result of the chain is determined by the **first policy that reaches a definitive accept or reject**. If the first policy reaches a term that accepts or rejects, the chain stops — the second policy is never consulted for that route.

The `&&` operator in JunOS is *not* "AND" in the Boolean sense. It means "continue evaluation into the next policy only if the current policy falls through without a decision".

Contrast with the `,` (comma) operator which sequences policies with OR logic — the first accept terminates the chain, but a reject in the first policy does not stop the chain from being evaluated by the second.

For Apstra configurations, `&&` is used when the first policy (`LEAF_TO_SPINE_FABRIC_OUT`) is intended to gate/filter, and only passing routes should proceed to the general acceptance policy (`BGP-AOS-Policy`).

#### Term Evaluation: First-Match, Not Best-Match

Inside a policy statement, terms are evaluated **in order, top to bottom, and the first term that BOTH matches AND has an explicit `then accept` or `then reject` action terminates evaluation of that policy**.

```
policy-statement LEAF_TO_SPINE_FABRIC_OUT {
    term LEAF_TO_SPINE_FABRIC_OUT-10 {
        from {
            community FROM_SPINE_FABRIC_TIER;   # Match condition
            protocol bgp;
        }
        then reject;                              # Terminate: route dropped
    }
    term LEAF_TO_SPINE_FABRIC_OUT-20 {
        then accept;                              # Catch-all: accept everything else
    }
}
```

- A route bearing community `0:15` AND learned via BGP matches term `-10` → rejected. Term `-20` is never reached for this route.
- A route that is directly connected (protocol `direct`) does NOT match term `-10` → falls through to term `-20` → accepted.
- A route learned via BGP that does NOT carry `0:15` does NOT match term `-10` (community match fails) → falls through → accepted.

**The implicit default action**: If a route falls through ALL terms in a policy without matching any, the result is the policy's **implicit default**. For export policies, the implicit default is **reject**. For import policies, the implicit default is also **reject**. This means a policy with no matching terms silently drops the route — a very common source of routes "disappearing".

#### The Hidden Final Reject

Every JunOS routing policy has an implicit final `then reject` appended after the last term. Apstra makes this explicit by always adding a numbered reject term at the end of every policy:

```
policy-statement BGP-AOS-Policy {
    term BGP-AOS-Policy-10 {
        from { policy AllPodNetworks; }
        then accept;
    }
    term BGP-AOS-Policy-20 {
        from { protocol bgp; }
        then accept;
    }
    term BGP-AOS-Policy-100 {
        then reject;    # Explicit, numbered — documents intent and prevents ambiguity
    }
}
```

The `-100` suffix term (by Apstra convention, high-numbered terms are catch-all/default terms) ensures that any route not explicitly accepted is dropped with a visible, auditable term. When troubleshooting, this term appearing in policy trace output confirms "the route reached the end of the policy and was dropped by the default reject" rather than potentially being accepted by an implicit default in a hypothetical future policy extension.

---

### 9.1b Common Routing Policy Failure Modes

#### Shadow Terms (Earlier Term Catches Everything)

The most frequent routing policy bug is a term that matches **more** routes than intended, preventing later terms from ever being reached:

```
# BROKEN EXAMPLE — term -10 matches ALL BGP routes including the ones
# term -20 was meant to catch specifically
policy-statement BROKEN_EXAMPLE {
    term term-10 {
        from { protocol bgp; }
        then accept;              # ← accepts ALL bgp routes here
    }
    term term-20 {
        from {
            protocol bgp;
            community SPECIAL_COMMUNITY;   # ← never reached for bgp routes
        }
        then reject;
    }
}
```

Term `-20` is **shadowed** by term `-10`. Any BGP route (including those with `SPECIAL_COMMUNITY`) matches `-10` first and is accepted. `-20` is dead code.

**Fix**: Place the more specific term first:
```
policy-statement FIXED_EXAMPLE {
    term term-10 {
        from {
            protocol bgp;
            community SPECIAL_COMMUNITY;   # specific first
        }
        then reject;
    }
    term term-20 {
        from { protocol bgp; }             # broad match second
        then accept;
    }
}
```

Apstra always places specific-match (reject) terms with lower numbers and broad-match (accept) terms with higher numbers, following the `-10` < `-20` < `-100` pattern.

#### `from` with Multiple Conditions is AND Logic

Inside a `from { }` block, all conditions must be satisfied simultaneously. This is a common misunderstanding:

```
term misunderstood {
    from {
        community FOO;
        protocol bgp;
    }
    then reject;
}
```

This rejects a route ONLY IF it has community `FOO` AND is learned via BGP. A route with community `FOO` that is directly connected (`protocol direct`) will NOT match this term. A BGP route without community `FOO` will NOT match.

This is how Apstra's loop-prevention works correctly — the term:
```
from {
    community FROM_SPINE_FABRIC_TIER;
    protocol bgp;
}
```
only rejects a route if it was learned from BGP **and** carries the spine community. Directly connected routes pass through regardless, which is correct — a leaf's own loopback (directly connected) should always be advertised to the spine even if somehow it were tagged.

#### Chained Policy Short-Circuit

When using `&&` chains, if the first policy **rejects** a route, the second policy is never evaluated. This catches people out with chains like:

```
export ( FILTER_POLICY && ACCEPT_POLICY );
```

If `FILTER_POLICY` explicitly rejects a route, `ACCEPT_POLICY` never runs. If `FILTER_POLICY` has no matching terms and falls through to its implicit reject, `ACCEPT_POLICY` is still not reached — the chain terminates.

In Apstra's design, `LEAF_TO_SPINE_FABRIC_OUT` only rejects specific routes (spine-learned routes); everything else falls through without a decision (no term matches). The chain continues to `BGP-AOS-Policy` for those routes. Routes rejected by `-10` are dropped before `BGP-AOS-Policy` sees them. This is the intended design.

#### `vpn-apply-export` and VRF Policy Application

Almost all BGP groups in Apstra configurations include `vpn-apply-export`. Without this, VRF-specific export policies would be ignored for EVPN and VPN routes. With `vpn-apply-export`, the route's export policy is evaluated in the context of the VRF table from which the route originates, not just the global BGP export policy. This is critical for VRF routes to carry their per-VRF community tags correctly.

#### Per-Neighbour vs Per-Group Export Policies

In Apstra configurations, export policies are applied **per-neighbour** when the neighbours need different treatment:

```
neighbor 172.16.100.2 {
    export ( RoutesToExt-default-DCI );
}
neighbor 172.16.200.2 {
    export ( RoutesToExt-default-DCI );    # Same policy, both DCI links
}
```

Or **per-group** when all neighbours in the group get the same policy:

```
group l3clos-s {
    ...
    # No per-neighbour policy override
}
neighbor 192.168.0.1 {
    export ( SPINE_TO_LEAF_FABRIC_OUT && BGP-AOS-Policy );  # per-neighbour
}
```

**Important**: A per-neighbour `export` overrides the group-level `export`. If both are defined, only the neighbour-level policy applies to that session. Apstra consistently uses per-neighbour policies for the leaf `l3clos-l` and spine `l3clos-s` groups, ensuring each peer relationship can be audited independently.

#### Route-Filter Lists vs Community Matching

Apstra uses both route-filter lists and community matching for route control. They serve different purposes and interact differently:

- **Route-filter lists** (`route-filter-list`) match on the route prefix itself (destination IP/length). They are used for WAN import/export policies to accept or reject specific subnets.
- **Community matching** matches on attributes attached to the route object. They are used for loop-prevention and route tagging.

A route-filter match is an AND condition with other `from` clauses. Example from a DCI export policy:
```
policy-statement RoutesToExt-default-DCI {
    term RoutesToExt-default-DCI-10 {
        from {
            route-filter-list RoutesToExt-default-DCI;  # prefix match
            family inet;                                  # AND must be IPv4
        }
        then {
            community delete FABRIC_COMMUNITIES;  # strip internal tags
            next-hop self;                         # make local leaf the BGP next-hop
            accept;
        }
    }
    term RoutesToExt-default-DCI-30 {
        from { family inet; }
        then reject;    # any other IPv4 not in the filter list is blocked
    }
}
```

The route-filter list defines exactly which prefixes are permitted to cross the DCI boundary. Any IPv4 route not in the list hits the `-30` reject term. This is a whitelist model — fail-closed — which is the correct security-conscious approach for DCI advertisement.

#### `next-hop self` and When It Matters

In DCI export policies, `next-hop self` rewrites the BGP next-hop to the local router's address before advertisement. Without this:
- The remote site would receive the route with the original next-hop (a loopback or IRB address inside DC1's fabric).
- That address is not reachable from DC2 via the DCI link.
- The remote DC would install the route but with an unresolvable next-hop — traffic blackhole.

With `next-hop self`, the border leaf presents itself as the next-hop for all routes it advertises over DCI. The remote DC only needs to know how to reach the border leaf, not the individual leafs inside the fabric.

In intra-fabric EVPN sessions, `no-nexthop-change` does the opposite: it deliberately preserves the original leaf loopback as the next-hop so the receiving leaf knows which VTEP to send VXLAN-encapsulated traffic to. These two knobs (`next-hop self` for external, `no-nexthop-change` for internal) are complementary design choices.

---

### 9.1c Full Loop-Prevention Trace: 3-Stage Example

To tie the policy concepts together, here is the complete community lifecycle for a single route in a 3-stage fabric:

**Scenario**: Leaf2 has a directly-connected subnet 10.80.101.0/24 on irb.101 (Prod_VRF). How does this route reach Leaf1?

1. **Leaf2 originates route**: 10.80.101.0/24, learn via `protocol direct` on irb.101 in `Prod_VRF`.

2. **Leaf2 global BGP export** to Spine1 (underlay `l3clos-l` group), export policy `(LEAF_TO_SPINE_FABRIC_OUT && BGP-AOS-Policy)`:
   - `LEAF_TO_SPINE_FABRIC_OUT` term `-10`: matches BGP routes with `FROM_SPINE_FABRIC_TIER (0:15)`. This route is `protocol direct`, not BGP — no match. Falls through.
   - `LEAF_TO_SPINE_FABRIC_OUT` term `-20`: catch-all accept — but this term has no `then` action, only an implicit fallthrough. Actually Apstra's version of this term explicitly accepts, so the chain stops here.
   - Wait — `BGP-AOS-Policy` is still evaluated via `&&` only if `LEAF_TO_SPINE_FABRIC_OUT` did not reach a terminal decision. In Apstra's actual implementation, `LEAF_TO_SPINE_FABRIC_OUT` term `-20` is `then accept` — so the chain terminates here and `BGP-AOS-Policy` is not reached. The route is accepted for advertisement.
   - **But**: `BGP-AOS-Policy` is evaluated separately because `AllPodNetworks` in term `-10` tags the route with `DEFAULT_DIRECT_V4` community. This tagging happens via `AllPodNetworks` being called as a policy match (`from { policy AllPodNetworks; }`), and the `then { community add DEFAULT_DIRECT_V4; accept; }` inside `AllPodNetworks` adds the community as a side effect when the route is matched.
   - Route is advertised to Spine1 with `DEFAULT_DIRECT_V4` community (e.g., `4:20007`).

3. **Spine1 receives route**: 10.80.101.0/24 from Leaf2, community `4:20007`.

4. **Spine1 re-advertises to Leaf1** (via `l3clos-s` group), export policy `(SPINE_TO_LEAF_FABRIC_OUT && BGP-AOS-Policy)`:
   - `SPINE_TO_LEAF_FABRIC_OUT` term `-10`: adds community `FROM_SPINE_FABRIC_TIER (0:15)` to all routes, accepts.
   - Route leaves Spine1 carrying both `4:20007` and `0:15`.

5. **Leaf1 receives route**: 10.80.101.0/24, communities `4:20007` and `0:15`.

6. **Leaf1 attempts to re-advertise to Spine1** (export policy `LEAF_TO_SPINE_FABRIC_OUT && BGP-AOS-Policy`):
   - Term `-10`: matches `community FROM_SPINE_FABRIC_TIER (0:15)` AND `protocol bgp` — **both conditions true** → `then reject`.
   - Route is dropped. **Loop prevented.**

7. **The same route** (10.80.101.0/24) is also advertised via EVPN Type 5 from Leaf2 through the parallel EVPN overlay path, using the `l3clos-l-evpn` group and `LEAF_TO_SPINE_EVPN_OUT && EVPN_EXPORT` policy with the same community-based loop prevention pattern using `FROM_SPINE_EVPN_TIER (0:14)`.

### 9.2 MTU Standards

All fabric links use **jumbo frames** to accommodate VXLAN overhead:

| Layer | MTU Value |
|---|---|
| Physical/Interface MTU | 9192 or 9216 |
| IP MTU (`family inet mtu`) | 9170 |
| IRB/overlay MTU | 9000 |

VXLAN adds 50 bytes of overhead (8-byte VXLAN header + 14-byte inner Ethernet + 20-byte outer IP + 8-byte UDP). The 22-byte difference between 9192 physical and 9170 IP accounts for this plus inner Ethernet headers.

### 9.3 ECMP and Load Balancing

Every device has a `PFE-LB` policy applied to the forwarding table:
```
policy-statement PFE-LB {
    then { load-balance per-packet; }
}
routing-options {
    forwarding-table {
        export PFE-LB;
        ecmp-fast-reroute;
    }
}
```
Despite being called "per-packet", on modern Juniper hardware this translates to per-flow (5-tuple hash) load balancing in the forwarding silicon. `ecmp-fast-reroute` enables rapid failover to an alternate ECMP path if a next-hop becomes unavailable.

Leafs also add:
```
chained-composite-next-hop {
    ingress { evpn; }
}
```
This enables **chained composite next-hop** for EVPN, allowing ECMP across multiple VXLAN tunnels to multi-homed remote endpoints (e.g., when a remote server is multi-homed via ESI to two leafs, traffic to it is sprayed across both VXLAN tunnels).

### 9.4 LLDP

All devices run LLDP (Link Layer Discovery Protocol):
```
protocols {
    lldp {
        port-id-subtype interface-name;
        port-description-type interface-description;
        neighbour-port-info-display port-id;
        interface all;
    }
}
```
Apstra uses LLDP discovery to auto-detect cabling and can validate that the physical topology matches the blueprint. The `interface-description` type ensures port descriptions (which Apstra sets to `facing_<neighbour>:<port>`) are transmitted in LLDP frames, enabling Apstra to correlate physical connections to intended blueprint links.

### 9.5 BFD (Bidirectional Forwarding Detection)

Two BFD timers appear in Apstra configs:

- **Underlay BGP BFD**: `minimum-interval 1000; multiplier 3;` → 3-second hold time for underlay failure detection. Fast enough to react to link/device failures and find an alternate ECMP path.
- **EVPN overlay BFD**: `minimum-interval 3000; multiplier 3;` → 9-second hold time. EVPN sessions are more tolerant because a lost EVPN session requires a full MAC/IP table rebuild; the slower timer prevents flapping on transient issues.
- `dont-help-shared-fate-bfd-down` — Prevents BGP from tearing down a session based on a BFD failure that was triggered by the same root-cause event (e.g., a line card reboot). This avoids cascading failures where BFD and BGP both react simultaneously to the same fault.

### 9.6 RSTP Behaviour

Spanning Tree Protocol behaviour differs by device role:

| Device | RSTP Configuration | Reason |
|---|---|---|
| Spine | `rstp { disable; }` | Pure L3 device — STP frames would be dropped anyway |
| Leaf (uplinks) | `bridge-priority 0; bpdu-block-on-edge;` | Leaf is STP root; server-facing ports blocked from BPDUs |
| Leaf (ESI LAG/server ports) | `interface <ae> { edge; }` | Edge ports — fast STP transition to forwarding for servers |
| Access switch | `bpdu-block-on-edge;` | Block BPDUs from server-facing ports |

### 9.7 Graceful Restart

All BGP sessions are configured with `graceful-restart`. This allows JunOS to maintain forwarding during a BGP process restart (e.g., software upgrade) without dropping data-plane traffic — the device continues forwarding based on a "stale" FIB while the control plane reconnects.

---

## 10. Configuration Section Quick Reference

This section is a rapid-reference guide to identify what any configuration stanza is doing at a glance.

### Interface Naming Conventions

| Prefix | Hardware type | Typical use |
|---|---|---|
| `ge-` | Gigabit Ethernet (1G) | 3-stage fabric links, server access |
| `xe-` | 10 Gigabit Ethernet | 5-stage leaf downlinks, server access |
| `et-` | 40/100 Gigabit Ethernet | 5-stage spine-to-superspine, leaf uplinks in 5-stage |
| `ae` | Aggregated Ethernet (LAG) | ESI multi-homing, WAN, access uplinks |
| `irb` | Integrated Routing and Bridging | L3 gateway for a VLAN within a fabric VRF |
| `lo0` | Loopback | Router-id, VTEP, VRF stable routes |

### BGP Group Name Dictionary

| Group | Where seen | Function |
|---|---|---|
| `l3clos-l` | Leaf, Collapsed Leaf | Underlay IPv4 to spines (or peer leaf in collapsed) |
| `l3clos-l-evpn` | Leaf, Collapsed Leaf | EVPN overlay to spine loopbacks |
| `l3clos-s` | Spine, Pod-Spine | Underlay to leafs AND (5-stage) superspines |
| `l3clos-s-evpn` | Spine, Pod-Spine | EVPN overlay to leafs AND (5-stage) superspines |
| `l3clos-a` | Access switch | Underlay to peer access switch |
| `l3clos-a-evpn` | Access switch | EVPN overlay to peer access switch loopback |
| `l3rtr` | Border Leaf, Collapsed Leaf | BGP to WAN routers or DCI peers (per-VRF) |
| `evpn-gw` | Border Leaf, Collapsed Leaf | EVPN gateway peering to remote DC border leafs |

### Key `routing-instances` Types

| Instance name pattern | Type | Purpose |
|---|---|---|
| `evpn-1` | `mac-vrf` | L2 switching — bridges VLANs to VXLANs; present on all leafs and access switches |
| `<VRF-name>` (e.g. `Prod_VRF`, `1BLUE`, `INTERNET`) | `vrf` | L3 tenant routing |

### `ip-prefix-routes` vs `vni-options`

- **`vni-options`** (in mac-vrf protocols evpn): Associates VNIs with route targets for L2 MAC/IP advertisement (EVPN Type 2 routes).
- **`ip-prefix-routes`** (in vrf protocols evpn): Advertises L3 connected subnets as EVPN Type 5 IP Prefix routes. Every L3 VRF uses this to distribute routing information via EVPN.

### `flexible-vlan-tagging` Interfaces

When you see `flexible-vlan-tagging; native-vlan-id 1;` on an interface with multiple `unit` stanzas, interpret this as: "This physical port carries multiple VLANs (one per logical unit), and each unit corresponds to a different VRF or routing context." This is used for WAN router connections and DCI links where multiple independent routing contexts must share a physical port.

### `no-arp-suppression` in access switch VLANs

On access switches, some VLANs have `no-arp-suppression;`:
```
vn3995 {
    vlan-id 3995;
    description "L2-DCI-VLAN3995";
    no-arp-suppression;
    vxlan { vni 300002; }
}
```
ARP suppression is an EVPN optimisation where the local leaf answers ARP requests on behalf of known remote hosts, reducing flood traffic. `no-arp-suppression` disables this and allows ARP to flood normally — typically used for L2 DCI VLANs where remote MAC/IP reachability via EVPN may not be complete or where applications are sensitive to proxy-ARP behaviour.

---

## Appendix: JunOS CLI Reference

The file `junos-cli-ref-list.txt` in this directory contains links to the Juniper official JunOS CLI Statement Reference documentation. Entries are one URL per line in alphabetical order by statement name. To look up a specific configuration statement, use `grep` or similar tools to search for the statement keyword. For example, to find documentation for EVPN interconnect:

```bash
grep -i "interconnect" junos-cli-ref-list.txt
grep -i "irb-symmetric" junos-cli-ref-list.txt
grep -i "translation-vni" junos-cli-ref-list.txt
```

Key JunOS CLI reference pages directly relevant to Apstra configurations:

| Statement | URL suffix in list |
|---|---|
| `evpn-vxlan` | `ref/statement/evpn-vxlan.html` |
| `extended-vni-list` | `ref/statement/extended-vni-list.html` |
| `irb-symmetric-routing` | `ref/statement/irb-symmetric-routing-protocols-evpn.html` |
| `interconnect` (EVPN) | `ref/statement/interconnect-edit-routing-instances-protocols-evpn.html` |
| `interconnected-vni-list` | `ref/statement/interconnected-vni-list-edit-routing-instances-protocols-evpn-interconnect.html` |
| `interconnect-multihoming-peer-gateways` | `ref/statement/interconnect-multihoming-peer-gateways-protocols-evpn.html` |
| `translation-vni` | `ref/statement/translation-vni-edit-routing-instances-vlans-vxlan.html` |
| `virtual-gateway-address` | `ref/statement/virtual-gateway-address-edit-interfaces.html` |
| `chained-composite-next-hop` | `ref/statement/chained-composite-next-hop-edit-routing-options.html` |
| `mac-vrf` | `ref/statement/mac-vrf.html` |
| `vxlan-routing` | `ref/statement/vxlan-routing-edit-forwarding-options.html` |
