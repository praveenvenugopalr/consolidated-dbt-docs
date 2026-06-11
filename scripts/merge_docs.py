"""
merge_docs.py

Merges dbt documentation artifacts from multiple projects into a single site.
Performs cross-project lineage stitching and injects Power BI exposures.

Usage:
    python merge_docs.py --env prod
    python merge_docs.py --env dev
"""

import argparse
import json
import os
import shutil
import sys
from parse_pbi import parse_pbi_project


def load_json(path):
    with open(path, "r") as f:
        return json.load(f)


def save_json(data, path):
    with open(path, "w") as f:
        json.dump(data, f)


def discover_projects(artifacts_dir):
    projects = {}
    if not os.path.isdir(artifacts_dir):
        return projects
    for entry in sorted(os.listdir(artifacts_dir)):
        entry_path = os.path.join(artifacts_dir, entry)
        if os.path.isdir(entry_path) and os.path.exists(os.path.join(entry_path, "manifest.json")):
            projects[entry] = entry_path
    return projects


def merge_manifests(projects):
    merged_manifest = None
    merged_catalog = None

    for project_name, target_dir in projects.items():
        manifest = load_json(os.path.join(target_dir, "manifest.json"))
        catalog = load_json(os.path.join(target_dir, "catalog.json"))

        if merged_manifest is None:
            merged_manifest = manifest
            merged_catalog = catalog
        else:
            for key in ["nodes", "sources", "macros", "docs", "exposures", "metrics", "selectors"]:
                if key in manifest and key in merged_manifest:
                    merged_manifest[key].update(manifest.get(key, {}))
            if "parent_map" in manifest:
                merged_manifest.setdefault("parent_map", {}).update(manifest["parent_map"])
            if "child_map" in manifest:
                merged_manifest.setdefault("child_map", {}).update(manifest["child_map"])

            for key in ["nodes", "sources"]:
                if key in catalog and key in merged_catalog:
                    merged_catalog[key].update(catalog.get(key, {}))

    return merged_manifest, merged_catalog


def build_fqn_lookups(merged_manifest):
    source_to_node = {}
    for source_key, source_val in merged_manifest.get("sources", {}).items():
        db = (source_val.get("database") or "").upper()
        schema = (source_val.get("schema") or "").upper()
        table = (source_val.get("name") or "").upper()
        fqn = f"{db}.{schema}.{table}"
        source_to_node.setdefault(fqn, []).append(source_key)

    node_fqn_map = {}
    for node_key, node_val in merged_manifest.get("nodes", {}).items():
        if not node_key.startswith("model."):
            continue
        db = (node_val.get("database") or "").upper()
        schema = (node_val.get("schema") or "").upper()
        alias = (node_val.get("alias") or node_val.get("name") or "").upper()
        node_fqn_map[f"{db}.{schema}.{alias}"] = node_key

    return source_to_node, node_fqn_map


def stitch_cross_project_lineage(merged_manifest, source_to_node, node_fqn_map):
    stitched = 0
    for fqn_key, source_keys in list(source_to_node.items()):
        if fqn_key not in node_fqn_map:
            continue

        node_key = node_fqn_map[fqn_key]

        for source_key in source_keys:
            for child_key in list(merged_manifest.get("child_map", {}).get(source_key, [])):
                if not (child_key.startswith("model.") or child_key.startswith("test.")):
                    continue

                child_node = merged_manifest.get("nodes", {}).get(child_key, {})
                deps = child_node.get("depends_on", {}).setdefault("nodes", [])
                if source_key in deps:
                    deps.remove(source_key)
                if node_key not in deps:
                    deps.append(node_key)

                merged_manifest.setdefault("parent_map", {}).setdefault(child_key, [])
                if source_key in merged_manifest["parent_map"][child_key]:
                    merged_manifest["parent_map"][child_key].remove(source_key)
                if node_key not in merged_manifest["parent_map"][child_key]:
                    merged_manifest["parent_map"][child_key].append(node_key)

                merged_manifest.setdefault("child_map", {}).setdefault(node_key, [])
                if child_key not in merged_manifest["child_map"][node_key]:
                    merged_manifest["child_map"][node_key].append(child_key)

            if source_key in merged_manifest.get("child_map", {}):
                del merged_manifest["child_map"][source_key]
            if source_key in merged_manifest.get("parent_map", {}):
                del merged_manifest["parent_map"][source_key]
            if source_key in merged_manifest.get("sources", {}):
                del merged_manifest["sources"][source_key]

            stitched += 1
            print(f"  Stitched: {source_key} → {node_key}")

    return stitched


def inject_pbi_exposures(merged_manifest, node_fqn_map, pbi_path, pbi_name):
    if not os.path.isdir(pbi_path):
        print(f"  Power BI repo not found: {pbi_path}, skipping")
        return 0

    pbi_project = parse_pbi_project(pbi_path)
    report_name = pbi_project["report_name"] or pbi_name
    print(f"\n  Power BI: {report_name} ({len(pbi_project['tables'])} tables, {len(pbi_project['relationships'])} relationships)")

    added = 0
    for table in pbi_project["tables"]:
        if table.is_calculated:
            continue

        sf_fqn = f"{table.snowflake_database}.{table.snowflake_schema}.{table.snowflake_table}".upper()
        upstream_model = node_fqn_map.get(sf_fqn)

        if not upstream_model:
            table_name_upper = (table.snowflake_table or table.name).upper()
            for nk, nv in merged_manifest.get("nodes", {}).items():
                if not nk.startswith("model."):
                    continue
                alias = (nv.get("alias") or nv.get("name") or "").upper()
                if alias == table_name_upper:
                    upstream_model = nk
                    break

        if not upstream_model:
            print(f"    WARN: No dbt model found for {sf_fqn} (PBI table: {table.name})")
            continue

        safe_report = report_name.lower().replace(" ", "_").replace("-", "_")
        safe_table = table.name.lower().replace(" ", "_").replace("-", "_")
        exposure_key = f"exposure.powerbi.{safe_report}.{safe_table}"

        measures_doc = ""
        if table.measures:
            measures_doc = "\n\n**DAX Measures:**\n\n| Measure | Formula | Format |\n|---------|---------|--------|\n"
            for m in table.measures:
                expr = m.expression.replace("\n", " ").replace("|", "\\|")[:80]
                measures_doc += f"| {m.name} | `{expr}` | {m.format_string} |\n"

        columns_doc = ""
        if table.columns:
            columns_doc = "\n\n**Columns:**\n\n| Column | Type | Summarize By |\n|--------|------|-------------|\n"
            for c in table.columns:
                columns_doc += f"| {c.name} | {c.data_type} | {c.summarize_by} |\n"

        description = f"Power BI table in **{report_name}**. Sources from `{sf_fqn}`.{measures_doc}{columns_doc}"

        exposure_node = {
            "name": f"{safe_report}_{safe_table}",
            "resource_type": "exposure",
            "package_name": "powerbi",
            "path": f"powerbi/{safe_report}/{safe_table}.yml",
            "original_file_path": f"models/powerbi/{safe_report}/{safe_table}.yml",
            "unique_id": exposure_key,
            "fqn": ["powerbi", safe_report, safe_table],
            "type": "dashboard",
            "owner": {"email": None, "name": "BI Team"},
            "description": description,
            "label": f"{report_name} - {table.name}",
            "maturity": "high",
            "meta": {
                "tool": "Power BI",
                "report": report_name,
                "table": table.name,
                "mode": table.mode,
                "snowflake_source": sf_fqn,
                "measures": [{"name": m.name, "expression": m.expression, "format": m.format_string} for m in table.measures],
                "columns": [{"name": c.name, "type": c.data_type} for c in table.columns],
            },
            "tags": ["powerbi", "dashboard"],
            "config": {"enabled": True, "tags": ["powerbi", "dashboard"], "meta": {}},
            "unrendered_config": {},
            "url": None,
            "depends_on": {"macros": [], "nodes": [upstream_model]},
            "refs": [
                {
                    "name": upstream_model.split(".")[-1],
                    "package": None,
                    "version": None,
                }
            ],
            "sources": [],
            "metrics": [],
            "created_at": 1780371916.0,
        }

        merged_manifest.setdefault("exposures", {})[exposure_key] = exposure_node
        merged_manifest.setdefault("child_map", {}).setdefault(upstream_model, [])
        if exposure_key not in merged_manifest["child_map"][upstream_model]:
            merged_manifest["child_map"][upstream_model].append(exposure_key)
        merged_manifest.setdefault("parent_map", {})[exposure_key] = [upstream_model]
        merged_manifest["child_map"][exposure_key] = []
        merged_manifest.setdefault("group_map", {})
        merged_manifest.setdefault("disabled", {})

        added += 1
        print(f"    Added: {table.name} → {upstream_model}")

    return added


def inject_pbi_from_json(merged_manifest, node_fqn_map, json_path, pbi_name):
    """Load pre-parsed PBI metadata JSON (produced by powerbi repo CI) and inject exposures."""
    if not os.path.isfile(json_path):
        print(f"  PBI metadata not found: {json_path}, skipping")
        return 0

    with open(json_path) as f:
        pbi_data = json.load(f)

    report_name = pbi_data.get("report_name") or pbi_name
    print(f"\n  Power BI (from artifact): {report_name} ({len(pbi_data['tables'])} tables, {len(pbi_data['relationships'])} relationships)")

    added = 0
    for table in pbi_data["tables"]:
        sf_fqn = (table.get("source_fqn") or "").upper()
        if not sf_fqn:
            continue

        upstream_model = node_fqn_map.get(sf_fqn)
        if not upstream_model:
            table_name_upper = sf_fqn.split(".")[-1] if "." in sf_fqn else table["name"].upper()
            for nk, nv in merged_manifest.get("nodes", {}).items():
                if not nk.startswith("model."):
                    continue
                alias = (nv.get("alias") or nv.get("name") or "").upper()
                if alias == table_name_upper:
                    upstream_model = nk
                    break

        if not upstream_model:
            continue

        safe_report = report_name.lower().replace(" ", "_").replace("-", "_")
        safe_table = table["name"].lower().replace(" ", "_").replace("-", "_")
        exposure_key = f"exposure.powerbi.{safe_report}.{safe_table}"

        measures_doc = ""
        if table.get("measures"):
            measures_doc = "\n\n**DAX Measures:**\n\n| Measure | Formula | Format |\n|---------|---------|--------|\n"
            for m in table["measures"]:
                expr = m["expression"].replace("\n", " ").replace("|", "\\|")[:80]
                measures_doc += f"| {m['name']} | `{expr}` | {m.get('format_string', '')} |\n"

        columns_doc = ""
        if table.get("columns"):
            columns_doc = "\n\n**Columns:**\n\n| Column | Type | Summarize By |\n|--------|------|-------------|\n"
            for c in table["columns"]:
                columns_doc += f"| {c['name']} | {c.get('data_type', '')} | {c.get('summarize_by', '')} |\n"

        description = f"Power BI table in **{report_name}**. Sources from `{sf_fqn}`.{measures_doc}{columns_doc}"

        exposure_node = {
            "name": f"{safe_report}_{safe_table}",
            "resource_type": "exposure",
            "package_name": "powerbi",
            "path": f"powerbi/{safe_report}/{safe_table}.yml",
            "original_file_path": f"models/powerbi/{safe_report}/{safe_table}.yml",
            "unique_id": exposure_key,
            "fqn": ["powerbi", safe_report, safe_table],
            "type": "dashboard",
            "owner": {"email": None, "name": "BI Team"},
            "description": description,
            "label": f"{report_name} - {table['name']}",
            "maturity": "high",
            "meta": {
                "tool": "Power BI",
                "report": report_name,
                "table": table["name"],
                "mode": table.get("mode", "import"),
                "snowflake_source": sf_fqn,
                "measures": table.get("measures", []),
                "columns": [{"name": c["name"], "type": c.get("data_type", "")} for c in table.get("columns", [])],
            },
            "tags": ["powerbi", "dashboard"],
            "config": {"enabled": True, "tags": ["powerbi", "dashboard"], "meta": {}},
            "unrendered_config": {},
            "url": None,
            "depends_on": {"macros": [], "nodes": [upstream_model]},
            "refs": [{"name": upstream_model.split(".")[-1], "package": None, "version": None}],
            "sources": [],
            "metrics": [],
            "created_at": 1780371916.0,
        }

        merged_manifest.setdefault("exposures", {})[exposure_key] = exposure_node
        merged_manifest.setdefault("child_map", {}).setdefault(upstream_model, [])
        if exposure_key not in merged_manifest["child_map"][upstream_model]:
            merged_manifest["child_map"][upstream_model].append(exposure_key)
        merged_manifest.setdefault("parent_map", {})[exposure_key] = [upstream_model]
        merged_manifest["child_map"][exposure_key] = []
        merged_manifest.setdefault("group_map", {})
        merged_manifest.setdefault("disabled", {})

        added += 1
        print(f"    Added: {table['name']} → {upstream_model}")

    return added


def main():
    parser = argparse.ArgumentParser(description="Merge dbt docs from multiple projects")
    parser.add_argument("--env", choices=["dev", "prod"], default="dev")
    args = parser.parse_args()

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    docs_dir = os.path.join(base_dir, "docs")
    artifacts_dir = os.path.join(base_dir, "artifacts", args.env)

    projects = discover_projects(artifacts_dir)
    if not projects:
        print(f"ERROR: No project artifacts found in {artifacts_dir}")
        sys.exit(1)

    print(f"\n{'='*50}")
    print(f"Building {args.env.upper()} documentation")
    print(f"{'='*50}")
    print(f"  Artifacts dir: {artifacts_dir}")
    print(f"  Projects: {list(projects.keys())}")

    merged_manifest, merged_catalog = merge_manifests(projects)
    source_to_node, node_fqn_map = build_fqn_lookups(merged_manifest)

    print(f"\n--- Cross-Project Lineage Stitching ---")
    stitched = stitch_cross_project_lineage(merged_manifest, source_to_node, node_fqn_map)
    print(f"  Total stitched: {stitched}")

    print(f"\n--- Power BI Integration ---")
    pbi_json_path = os.path.join(artifacts_dir, "powerbi", "pbi_metadata.json")
    pbi_tmdl_path = os.path.join(base_dir, "powerbi-project")
    total_exposures = 0
    if os.path.isfile(pbi_json_path):
        total_exposures += inject_pbi_from_json(merged_manifest, node_fqn_map, pbi_json_path, "finance_report")
    elif os.path.isdir(pbi_tmdl_path):
        total_exposures += inject_pbi_exposures(merged_manifest, node_fqn_map, pbi_tmdl_path, "finance_report")
    else:
        print("  No Power BI source found (neither artifact JSON nor TMDL repo)")
    print(f"  Total PBI exposures: {total_exposures}")

    os.makedirs(docs_dir, exist_ok=True)
    save_json(merged_manifest, os.path.join(docs_dir, f"manifest_{args.env}.json"))
    save_json(merged_catalog, os.path.join(docs_dir, f"catalog_{args.env}.json"))

    index_src = os.path.join(list(projects.values())[0], "index.html")
    index_dst = os.path.join(docs_dir, "index.html")
    if not os.path.exists(index_dst) and os.path.exists(index_src):
        shutil.copy2(index_src, index_dst)

    print(f"\n--- Output ---")
    print(f"  manifest_{args.env}.json: {os.path.getsize(os.path.join(docs_dir, f'manifest_{args.env}.json')) / 1024:.0f} KB")
    print(f"  catalog_{args.env}.json: {os.path.getsize(os.path.join(docs_dir, f'catalog_{args.env}.json')) / 1024:.0f} KB")
    print(f"  Nodes: {len(merged_manifest.get('nodes', {}))}")
    print(f"  Sources: {len(merged_manifest.get('sources', {}))}")
    print(f"  Exposures: {len(merged_manifest.get('exposures', {}))}")


if __name__ == "__main__":
    main()
