import os
import logging
import yaml
import re
from rdflib import Graph, XSD, Literal, URIRef, OWL, RDFS, RDF
from utils import get_first_literal, hyperlink_class, insert_spaces, class_restrictions, iter_annotations, DESC_PROPS
from rdflib.namespace import DCTERMS, SKOS

log = logging.getLogger("ofn2mkdocs")

class SafeMkDocsLoader(yaml.SafeLoader):
    """Custom YAML loader to handle MkDocs-specific python/name tags."""
    def ignore_python_name(self, node):
        """Treat python/name tags as strings."""
        return self.construct_scalar(node)

yaml.SafeLoader.add_constructor('tag:yaml.org,2002:python/name:material.extensions.emoji.twemoji', SafeMkDocsLoader.ignore_python_name)
yaml.SafeLoader.add_constructor('tag:yaml.org,2002:python/name:pymdownx.superfences.fence_code_format', SafeMkDocsLoader.ignore_python_name)

def get_specializations(g: Graph, cls: URIRef, global_all_classes: set, ns: str, prefix_map: dict) -> list:
    """Find all subclasses (direct and indirect) of the given class."""
    # Temporarily set logging to DEBUG
    # original_level = log.getEffectiveLevel()
    # log.setLevel(logging.DEBUG)
    
    specializations = []
    # Collect all subclasses using transitive closure
    visited = set()
    def collect_subclasses(c):
        if c in visited:
            return
        visited.add(c)
        for s in g.subjects(RDFS.subClassOf, c):
            if isinstance(s, URIRef) and s != c:
                cls_name = get_first_literal(g, s, [RDFS.label]) or str(s).split('/')[-1].split('#')[-1]
                if cls_name in global_all_classes:
                    desc = get_first_literal(g, s, [DCTERMS.description]) or ""
                    specializations.append((cls_name, desc))
                    collect_subclasses(s)

    collect_subclasses(cls)
    log.debug(f"Specializations for {cls}: {specializations}")
    
    # Restore original logging level
    # log.setLevel(original_level)
    return sorted(specializations, key=lambda x: x[0].lower())

def get_used_by(g: Graph, cls: URIRef, global_all_classes: set, ns: str, prefix_map: dict) -> list:
    """Find classes and their properties that reference this class via object property restrictions."""
    # Temporarily set logging to DEBUG
    # original_level = log.getEffectiveLevel()
    # log.setLevel(logging.DEBUG)
    
    used_by = []
    # Check all OWL restrictions
    for s in g.subjects(RDF.type, OWL.Restriction):
        prop = g.value(s, OWL.onProperty)
        for predicate in [OWL.allValuesFrom, OWL.someValuesFrom, OWL.hasValue]:
            target = g.value(s, predicate)
            if target == cls and prop:
                prop_name = get_first_literal(g, prop, [RDFS.label]) or str(prop).split('/')[-1].split('#')[-1]
                # Find classes that use this restriction
                for cls_sub in g.subjects(RDFS.subClassOf, s):
                    if isinstance(cls_sub, URIRef):
                        cls_name = get_first_literal(g, cls_sub, [RDFS.label]) or str(cls_sub).split('/')[-1].split('#')[-1]
                        if cls_name in global_all_classes:
                            used_by.append((cls_name, prop_name))
                # Check if the restriction itself is a named class
                cls_name = get_first_literal(g, s, [RDFS.label]) or str(s).split('/')[-1].split('#')[-1]
                if cls_name in global_all_classes:
                    used_by.append((cls_name, prop_name))

    log.debug(f"Used by for {cls}: {used_by}")
    
    # Restore original logging level
    # log.setLevel(original_level)
    return sorted(used_by, key=lambda x: x[0].lower())

def generate_markdown(g: Graph, cls: URIRef, cls_name: str, global_patterns: dict, global_all_classes: set, ns: str, ttl_path: str, errors: list, prefix_map: dict, prop_map: dict):
    """Generate Markdown file for a class."""
    classes_dir = os.path.join(os.path.dirname(ttl_path), "classes")
    filename = os.path.join(classes_dir, f"{cls_name}.md")
    
    # Temporarily set logging to DEBUG
    # original_level = log.getEffectiveLevel()
    # log.setLevel(logging.DEBUG)
    
    log.debug(f"Writing {filename} for class {cls_name} ({cls})")
    
    # Check if this is a pattern class
    is_pattern = cls_name in global_patterns

    if is_pattern:
        # Pattern class Markdown
        title = f"# {insert_spaces(cls_name)}\n\n"
        desc = get_first_literal(g, cls, [DCTERMS.description]) or ""
        top_desc = f"{desc}\n\n" if desc else ""
        member_classes = sorted(global_patterns[cls_name]["classes"], key=str.lower)
        members_md = "It consists of the following classes:\n\n"
        for mem_cls_name in member_classes:
            if mem_cls_name == 'ITSThing':
                continue  # Skip problematic classes
            display_mem = insert_spaces(mem_cls_name)
            members_md += f"- [{display_mem}]({mem_cls_name}.md)\n"
        content = title + top_desc + members_md
    else:
        # Non-pattern class Markdown
        title = f"# {cls_name}\n\n"
        desc = get_first_literal(g, cls, [DCTERMS.description]) or ""
        top_desc = f"{desc}\n\n" if desc else ""
        note = get_first_literal(g, cls, [SKOS.note]) or ""
        note_md = f"NOTE: {note}\n\n" if note else ""
        example = get_first_literal(g, cls, [SKOS.example]) or ""
        example_md = f"EXAMPLE: {example}\n\n" if example else ""
        diagram_line = f"![{cls_name} Diagram](../diagrams/{cls_name}.svg)\n\n<a href=\"../../diagrams/{cls_name}.svg\">Open interactive {cls_name} diagram</a>\n\n"
        
        # Specializations section
        specializations = get_specializations(g, cls, global_all_classes, ns, prefix_map)
        specializations_md = ""
        if specializations:
            specializations_md += f"## Specializations of {cls_name}\n\n"
            specializations_md += "| Class | Description |\n"
            specializations_md += "|-------|-------------|\n"
            for spec_cls, spec_desc in specializations:
                display_spec = insert_spaces(spec_cls)
                specializations_md += f"| [{display_spec}]({spec_cls}.md) | {spec_desc} |\n"
            specializations_md += "\n"
        else:
            log.debug(f"No specializations found for {cls_name}")
        
        # Formalization section
        restr_rows = class_restrictions(g, cls, ns, prefix_map)
        formalization_md = ""
        if restr_rows:
            formalization_md += f"## Formalization for {cls_name}\n\n"
            formalization_md += "| Property | Value Restriction | Definition |\n"
            formalization_md += "|----------|-------------------|------------|\n"
            for prop, restr in sorted(restr_rows):
                # Hyperlink local classes (no prefix)
                restr_hlinked = re.sub(r'\b([A-Z][a-zA-Z0-9]*)\b', lambda m: hyperlink_class(m.group(0), global_all_classes, ns) if m.group(0) not in ['or', 'exactly', 'min', 'max'] else m.group(0), restr)
                # Get property description
                prop_uri = prop_map.get(prop)
                prop_desc = get_first_literal(g, prop_uri, DESC_PROPS) if prop_uri else "---"
                formalization_md += f"| {prop} | {restr_hlinked} | {prop_desc} |\n"
            formalization_md += "\n"
        
        # Used By section
        used_by = get_used_by(g, cls, global_all_classes, ns, prefix_map)
        used_by_md = ""
        if used_by:
            used_by_md += f"## {cls_name} Is Used By\n\n"
            for used_cls, prop_name in used_by:
                used_by_md += f"- [{used_cls}]({used_cls}.md).{prop_name}\n"
            used_by_md += "\n"
        else:
            log.debug(f"No used by classes found for {cls_name}")
        
        other_ann = list(iter_annotations(g, cls, ns, prefix_map))
        other_md = ""
        if other_ann:
            other_md += "## Other Annotations\n\n"
            for p, v in sorted(other_ann):
                if p == 'xsd:pattern':
                    v = hyperlink_class(v, global_all_classes, ns)
                other_md += f"- **{p}**: {v}\n"
            other_md += "\n"
        content = title + top_desc + note_md + example_md + diagram_line + specializations_md + formalization_md + used_by_md + other_md

    # Restore original logging level
    # log.setLevel(original_level)
    
    # Write the Markdown file
    try:
        with open(filename, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        error_msg = f"Error writing Markdown for {cls_name} from {ttl_path}: {str(e)}\n{traceback.format_exc()}"
        errors.append(error_msg)
        log.error(error_msg)
        raise

def update_mkdocs_nav(mkdocs_path: str, global_patterns: dict, global_all_classes: set, errors: list):
    """Update mkdocs.yml navigation section."""
    try:
        with open(mkdocs_path, 'r', encoding="utf-8") as f:
            config = yaml.load(f, Loader=SafeMkDocsLoader)
    except Exception as e:
        error_msg = f"Error reading mkdocs.yml: {str(e)}\n{traceback.format_exc()}"
        errors.append(error_msg)
        log.error(error_msg)
        raise

    # Create navigation structure
    new_nav = [{"Home": "index.md"}]
    pattern_names = set(global_patterns.keys())
    for pat_cls_name in sorted(pattern_names, key=str.lower):
        if pat_cls_name == 'ITSThing':
            continue  # Skip problematic classes
        display_pat = insert_spaces(pat_cls_name)
        sub_nav = [{"Overview": f"classes/{pat_cls_name}.md"}]
        member_classes = sorted(global_patterns[pat_cls_name]["classes"], key=str.lower)
        for mem_cls_name in member_classes:
            if mem_cls_name == 'ITSThing':
                continue  # Skip problematic classes
            display_mem = insert_spaces(mem_cls_name)
            sub_nav.append({display_mem: f"classes/{mem_cls_name}.md"})
        new_nav.append({display_pat: sub_nav})

    # Add non-pattern classes
    pattern_members = set(sum([data["classes"] for data in global_patterns.values()], []))
    non_pattern_classes = sorted(
        [cls for cls in global_all_classes - pattern_members - pattern_names if ':' not in cls],
        key=str.lower
    )
    for cls_name in non_pattern_classes:
        if cls_name == 'ITSThing':
            continue  # Skip problematic classes
        display_cls = insert_spaces(cls_name)
        new_nav.append({display_cls: f"classes/{cls_name}.md"})

    config["nav"] = new_nav
    try:
        with open(mkdocs_path, 'w', encoding="utf-8") as f:
            yaml.safe_dump(config, f, sort_keys=False, allow_unicode=True)
    except Exception as e:
        error_msg = f"Error writing mkdocs.yml: {str(e)}\n{traceback.format_exc()}"
        errors.append(error_msg)
        log.error(error_msg)
        raise

def generate_index(docs_dir: str, ofn_files: list, ontology_info: dict, global_patterns: dict, errors: list):
    """Generate index.md file."""
    index_path = os.path.join(docs_dir, "index.md")
    index_content = ""

    if len(ofn_files) == 1:
        # Single OFN file case
        ttl_path = ofn_files[0]
        if ttl_path not in ontology_info:
            error_msg = f"Skipping index.md generation for {ttl_path} due to earlier processing failure"
            errors.append(error_msg)
            log.error(error_msg)
            return
        ttl_filename = os.path.basename(ttl_path)
        ofn_filename = ttl_filename.rsplit('.', 1)[0] + '.ofn'
        title = ontology_info[ttl_path]["title"]
        description = ontology_info[ttl_path]["description"]
        patterns = sorted(ontology_info[ttl_path]["patterns"], key=str.lower)
        pattern_names = set(global_patterns.keys())
        non_pattern_classes = sorted(ontology_info[ttl_path]["non_pattern_classes"] - pattern_names, key=str.lower)

        index_content += f"# {title}\n\n"
        if description:
            index_content += f"{description}\n\n"
        index_content += "This ontology consists of the following patterns:\n\n"
        for pat_name in patterns:
            if pat_name == 'ITSThing':
                continue  # Skip problematic classes
            display_pat = insert_spaces(pat_name)
            index_content += f"- [{display_pat}](classes/{pat_name}.md)\n"
        if non_pattern_classes:
            index_content += "\nThe ontology also contains the following classes that are not assigned to any pattern:\n\n"
            for cls_name in non_pattern_classes:
                if cls_name == 'ITSThing':
                    continue  # Skip problematic classes
                display_cls = insert_spaces(cls_name)
                index_content += f"- [{display_cls}](classes/{cls_name}.md)\n"
        index_content += f"\nThe formal definition of these patterns is available in [OWL Functional Notation]({ofn_filename}) and  [Turtle Syntax]({ttl_filename}).\n"
    else:
        # Multiple OFN files case
        readme_path = os.path.join(os.path.dirname(docs_dir), "README.md")
        if os.path.exists(readme_path):
            try:
                with open(readme_path, 'r', encoding="utf-8") as f:
                    first_line = f.readline().strip()
                    title = first_line.lstrip('#').strip() if first_line.startswith('#') else first_line
            except Exception as e:
                error_msg = f"Error reading README.md: {str(e)}\n{traceback.format_exc()}"
                errors.append(error_msg)
                log.error(error_msg)
                title = "No README.md file found for title"
        else:
            title = "No README.md file found for title"

        index_content += f"# {title}\n\n"
        for ttl_path in sorted(ofn_files):
            if ttl_path not in ontology_info:
                error_msg = f"Skipping index.md generation for {ttl_path} due to earlier processing failure"
                errors.append(error_msg)
                log.error(error_msg)
                continue
            ttl_filename = os.path.basename(ttl_path)
            ofn_filename = ttl_filename.rsplit('.', 1)[0] + '.ofn'
            title = ontology_info[ttl_path]["title"] or "Unknown Title"
            description = ontology_info[ttl_path]["description"] or "Unknown description"
            if not ontology_info[ttl_path]["title"]:
                log.warning("The ontology %s is missing a dc:title annotation.", ttl_filename)
            if not ontology_info[ttl_path]["description"]:
                log.warning("The ontology %s is missing a dcterms:description annotation.", ttl_filename)
            patterns = sorted(ontology_info[ttl_path]["patterns"], key=str.lower)
            pattern_names = set(global_patterns.keys())
            non_pattern_classes = sorted(ontology_info[ttl_path]["non_pattern_classes"] - pattern_names, key=str.lower)

            index_content += f"## {title}\n\n"
            if description:
                index_content += f"{description}\n\n"
            index_content += "This ontology consists of the following patterns:\n\n"
            for pat_name in patterns:
                if pat_name == 'ITSThing':
                    continue  # Skip problematic classes
                display_pat = insert_spaces(pat_name)
                index_content += f"- [{display_pat}](classes/{pat_name}.md)\n"
            if non_pattern_classes:
                index_content += "\nThe ontology also contains the following classes that are not assigned to any pattern:\n\n"
                for cls_name in non_pattern_classes:
                    if cls_name == 'ITSThing':
                        continue  # Skip problematic classes
                    display_cls = insert_spaces(cls_name)
                    index_content += f"- [{display_cls}](classes/{cls_name}.md)\n"
            index_content += f"\nThe formal definition of these patterns is available in [OWL Functional Notation]({ofn_filename}) and  [Turtle Syntax]({ttl_filename}).\n\n"

    # Write index.md
    try:
        with open(index_path, "w", encoding="utf-8") as f:
            f.write(index_content)
        log.info("Generated index.md at %s", index_path)
    except Exception as e:
        error_msg = f"Error writing index.md: {str(e)}\n{traceback.format_exc()}"
        errors.append(error_msg)
        log.error(error_msg)
        raise