"""
parse_pbi.py

Parses Power BI projects in PBIP/TMDL format to extract:
- Tables (with Snowflake source connections)
- Columns (types, descriptions, summarize-by)
- DAX Measures (formulas, format strings)
- Relationships (foreign keys between tables)

Requires Power BI projects saved in PBIP format (File → Save As → Power BI Project).
The PBIP format stores the semantic model as .tmdl text files, making them git-friendly
and parseable.

Usage:
    from parse_pbi import parse_pbi_project
    project = parse_pbi_project("/path/to/powerbi-project")
"""

import os
import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PBIMeasure:
    name: str
    expression: str
    format_string: str = ""
    description: str = ""


@dataclass
class PBIColumn:
    name: str
    data_type: str = ""
    source_column: str = ""
    description: str = ""
    format_string: str = ""
    summarize_by: str = "none"


@dataclass
class PBIRelationship:
    name: str
    from_table: str
    from_column: str
    to_table: str
    to_column: str


@dataclass
class PBITable:
    name: str
    columns: list = field(default_factory=list)
    measures: list = field(default_factory=list)
    snowflake_database: str = ""
    snowflake_schema: str = ""
    snowflake_table: str = ""
    is_calculated: bool = False
    mode: str = ""


def parse_tmdl_table(file_path: str) -> Optional[PBITable]:
    with open(file_path, "r") as f:
        content = f.read()

    table_match = re.match(r"table\s+'?([^'\n]+)'?", content)
    if not table_match:
        return None

    table = PBITable(name=table_match.group(1))

    measure_pattern = re.compile(
        r"\tmeasure\s+'([^']+)'\s*=\s*(.+?)(?=\n\t\t\w|\n\tmeasure|\n\tcolumn|\n\tpartition|\Z)",
        re.DOTALL
    )
    for m in measure_pattern.finditer(content):
        measure = PBIMeasure(name=m.group(1), expression=m.group(2).strip())
        block = content[m.start():m.end()]
        fmt = re.search(r"formatString:\s*(.+)", block)
        if fmt:
            measure.format_string = fmt.group(1).strip()
        desc = re.search(r"description:\s*(.+)", block)
        if desc:
            measure.description = desc.group(1).strip()
        table.measures.append(measure)

    col_pattern = re.compile(
        r"\tcolumn\s+(\w+)(.*?)(?=\n\tcolumn|\n\tmeasure|\n\tpartition|\Z)",
        re.DOTALL
    )
    for c in col_pattern.finditer(content):
        col = PBIColumn(name=c.group(1))
        block = c.group(2)
        dt = re.search(r"dataType:\s*(\w+)", block)
        if dt:
            col.data_type = dt.group(1)
        sc = re.search(r"sourceColumn:\s*(.+)", block)
        if sc:
            col.source_column = sc.group(1).strip()
        desc = re.search(r"description:\s*(.+)", block)
        if desc:
            col.description = desc.group(1).strip()
        fmt = re.search(r"formatString:\s*(.+)", block)
        if fmt:
            col.format_string = fmt.group(1).strip()
        sb = re.search(r"summarizeBy:\s*(\w+)", block)
        if sb:
            col.summarize_by = sb.group(1)
        table.columns.append(col)

    partition_block = re.search(
        r"\tpartition\s+.+?source\s*=\s*\n(.*?)(?=\n\t[a-z]|\Z)", content, re.DOTALL
    )
    if partition_block:
        src = partition_block.group(1)
        parts = re.findall(r'\{[^}]*Name="([^"]+)"[^}]*\}\[Data\]', src)
        if len(parts) >= 3:
            table.snowflake_database = parts[0]
            table.snowflake_schema = parts[1]
            table.snowflake_table = parts[2]
        elif len(parts) == 2:
            table.snowflake_schema = parts[0]
            table.snowflake_table = parts[1]

    calc_check = re.search(r"partition\s+'[^']+'\s*=\s*calculated", content)
    if calc_check:
        table.is_calculated = True

    mode_match = re.search(r"mode:\s*(\w+)", content)
    if mode_match:
        table.mode = mode_match.group(1)

    return table


def parse_tmdl_relationships(file_path: str) -> list:
    relationships = []
    with open(file_path, "r") as f:
        content = f.read()

    rel_pattern = re.compile(
        r"relationship\s+(\w+)\s*\n\tfromColumn:\s*([^.\n]+)\.(\w+)\s*\n\ttoColumn:\s*'?([^'.\n]+)'?\.(\w+)",
        re.MULTILINE
    )
    for m in rel_pattern.finditer(content):
        relationships.append(PBIRelationship(
            name=m.group(1),
            from_table=m.group(2),
            from_column=m.group(3),
            to_table=m.group(4),
            to_column=m.group(5),
        ))
    return relationships


def parse_pbi_project(project_path: str) -> dict:
    result = {"tables": [], "relationships": [], "report_name": ""}

    pbip_files = [f for f in os.listdir(project_path) if f.endswith(".pbip")]
    if pbip_files:
        result["report_name"] = pbip_files[0].replace(".pbip", "")

    semantic_dirs = [d for d in os.listdir(project_path) if "SemanticModel" in d]
    if not semantic_dirs:
        return result

    sem_path = os.path.join(project_path, semantic_dirs[0], "definition")

    tables_dir = os.path.join(sem_path, "tables")
    if os.path.isdir(tables_dir):
        for fname in sorted(os.listdir(tables_dir)):
            if fname.endswith(".tmdl"):
                table = parse_tmdl_table(os.path.join(tables_dir, fname))
                if table:
                    result["tables"].append(table)

    rel_file = os.path.join(sem_path, "relationships.tmdl")
    if os.path.isfile(rel_file):
        result["relationships"] = parse_tmdl_relationships(rel_file)

    return result
