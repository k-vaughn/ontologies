import os
import re
import logging
import sys
from typing import Optional, Iterable, Tuple, List
from rdflib import Graph, RDF, RDFS, OWL, XSD, URIRef, Literal
from rdflib.namespace import DC, DCTERMS, SKOS
from graphviz import Digraph
from collections.abc import Mapping
from funowl.converters.functional_converter import to_python
from collections import defaultdict
import yaml

# -------------------- logging --------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger("ofn2mkdocs")

# -------------------- YAML Custom Loader --------------------
class SafeMkDocsLoader(yaml.SafeLoader):
    """Custom YAML loader to handle MkDocs-specific python/name tags."""
    def ignore_python_name(self, node):
        """Treat python/name tags as strings."""
        return self.construct_scalar(node)

# Register the custom constructor for python/name tags
yaml.SafeLoader.add_constructor('tag:yaml.org,2002:python/name:material.extensions.emoji.twemoji', SafeMkDocsLoader.ignore_python_name)
yaml.SafeLoader.add_constructor('tag:yaml.org,2002:python/name:pymdownx.superfences.fence_code_format', SafeMkDocsLoader.ignore_python_name)

# -------------------- helpers --------------------
def get_prefix_named_pairs(ontology_doc, ns: str):
    """Return [{'prefix': <str>, 'uri': <str>}, ...] from funowl PrefixDeclarations,
    handling different return shapes of as_prefixes() across funowl versions."""
    pd = getattr(ontology_doc, "prefixDeclarations", None)
    if not pd:
        return [{"prefix": "", "uri": ns}]

    ap = pd.as_prefixes()
    out = []

    if hasattr(ap, "items"):
        out = [{"prefix": str(k), "uri": str(v)} for k, v in ap.items()]
    else:
        for item in ap:
            if isinstance(item, tuple) and len(item) == 2:
                k, v = item
                out.append({"prefix": str(k), "uri": str(v)})
                log.debug("      %s %s", k, v)
                continue

            p = (
                getattr(item, "prefixName", None)
                or getattr(item, "prefix", None)
                or getattr(item, "name", None)
            )
            iri = (
                getattr(item, "fullIRI", None)
                or getattr(item, "iri", None)
                or getattr(item, "iriRef", None)
            )
            if p is not None and iri is not None:
                out.append({"prefix": str(p), "uri": str(iri)})

    if not any(d["uri"] == ns for d in out):
        out.append({"prefix": "", "uri": ns})

    return out

def _norm_base(u: str) -> str:
    return u.rstrip('/#')

def get_qname(g: Graph, uri: URIRef, ns: str):
    s = str(uri)
    norm = _norm_base(s)
    if norm == _norm_base(ns) or s.startswith(ns):
        local = s[len(_norm_base(ns)):]
        if local.startswith(('/', '#', '_')):
            local = local[1:]
        return local.rstrip()
    for base in sorted(prefix_map.keys(), key=len, reverse=True):
        if norm == base or norm.startswith(base + '/') or norm.startswith(base + '#'):
            local = s[len(base):]
            if local.startswith(('/', '#', '_')):
                local = local[1:]
            local = local.rstrip()
            if prefix_map[base] == ":":
                return local
            return prefix_map[base] + local
    log.warning("No prefix found for URI: %s", s)
    return s

def get_label(g: Graph, c: URIRef) -> str:
    for _, _, lbl in g.triples((c, RDFS.label, None)):
        if isinstance(lbl, Literal):
            return str(lbl)
    return str(c).split('#')[-1]

def get_first_literal(g: Graph, subj: URIRef, preds: Iterable[URIRef]) -> Optional[str]:
    for p in preds:
        for _, _, lit in g.triples((subj, p, None)):
            if isinstance(lit, Literal):
                return str(lit)
    return None

def get_ontology_metadata(g: Graph, ns: str, predicate: URIRef) -> Optional[str]:
    """Extract metadata (e.g., dc:title, dcterms:description) from any subject in the graph."""
    for subj in g.subjects(predicate=predicate):
        for _, _, lit in g.triples((subj, predicate, None)):
            if isinstance(lit, Literal):
                return str(lit)
    # Try the ontology IRI directly
    ontology_iri = URIRef(ns.rstrip('#/'))
    for _, _, lit in g.triples((ontology_iri, predicate, None)):
        if isinstance(lit, Literal):
            return str(lit)
    return None

def iter_annotations(g: Graph, subj: URIRef) -> Iterable[Tuple[str, str]]:
    """Yield (predicate, value) pairs for annotations as readable strings,
    excluding those in SKIP_IN_OTHER."""
    for p, o in g.predicate_objects(subj):
        if not isinstance(p, URIRef):
            continue
        if p in SKIP_IN_OTHER:
            continue
        if isinstance(o, Literal):
            yield (get_qname(g, p, ns), str(o))

def get_union_classes(g: Graph, union: URIRef, ns: str) -> List[str]:
    classes = []
    current = g.value(union, OWL.unionOf)
    while current and current != RDF.nil:
        first = g.value(current, RDF.first)
        if first:
            classes.append(get_qname(g, first, ns))
        current = g.value(current, RDF.rest)
    return sorted(classes)

def class_restrictions(g: Graph, c: URIRef) -> List[Tuple[str, str]]:
    """Extract OWL restrictions and subClassOf constraints for Markdown output."""
    rows = []
    # Handle basic rdfs:subClassOf
    for _, _, super_cls in g.triples((c, RDFS.subClassOf, None)):
        if isinstance(super_cls, URIRef) and super_cls != OWL.Thing and (super_cls, RDF.type, OWL.Class) in g:
            rows.append(("rdfs:subClassOf", get_qname(g, super_cls, ns)))

    # Handle OWL restrictions
    for _, _, restr in g.triples((c, RDFS.subClassOf, None)):
        if (restr, RDF.type, OWL.Restriction) in g:
            on_prop = None
            for _, _, onp in g.triples((restr, OWL.onProperty, None)):
                if isinstance(onp, URIRef):
                    on_prop = get_qname(g, onp, ns)
            # AllValuesFrom
            all_values_from = g.value(restr, OWL.allValuesFrom)
            if all_values_from:
                if g.value(all_values_from, OWL.unionOf):
                    union_classes = get_union_classes(g, all_values_from, ns)
                    rows.append((on_prop, f"only ({' or '.join(union_classes)})"))
                else:
                    rows.append((on_prop, f"only {get_qname(g, all_values_from, ns)}"))
            # Qualified cardinalities for object properties
            on_class = g.value(restr, OWL.onClass)
            qualified_card = g.value(restr, OWL.qualifiedCardinality)
            min_qualified_card = g.value(restr, OWL.minQualifiedCardinality)
            max_qualified_card = g.value(restr, OWL.maxQualifiedCardinality)
            if on_prop and (qualified_card or min_qualified_card or max_qualified_card):
                label_parts = []
                if qualified_card and on_class:
                    label_parts.append(f"exactly {qualified_card}")
                if min_qualified_card and on_class:
                    label_parts.append(f"min {min_qualified_card}")
                if max_qualified_card and on_class:
                    label_parts.append(f"max {max_qualified_card}")
                if label_parts:
                    target_qname = get_qname(g, on_class, ns)
                    rows.append((on_prop, f"{' '.join(label_parts)} {target_qname}"))
            # Qualified cardinalities for data properties
            on_data_range = g.value(restr, OWL.onDataRange)
            if on_prop and (qualified_card or min_qualified_card or max_qualified_card):
                label_parts = []
                if qualified_card and on_data_range:
                    label_parts.append(f"exactly {qualified_card}")
                if min_qualified_card and on_data_range:
                    label_parts.append(f"min {min_qualified_card}")
                if max_qualified_card and on_data_range:
                    label_parts.append(f"max {max_qualified_card}")
                if label_parts:
                    range_name = get_qname(g, on_data_range, ns)
                    rows.append((on_prop, f"{' '.join(label_parts)} {range_name}"))
            # Non-qualified cardinalities
            card = g.value(restr, OWL.cardinality)
            min_card = g.value(restr, OWL.minCardinality)
            max_card = g.value(restr, OWL.maxCardinality)
            if card:
                range_type = g.value(on_prop, RDFS.range) if on_prop else None
                if range_type:
                    range_name = get_qname(g, range_type, ns)
                    rows.append((on_prop, f"exactly {card} {range_name}"))
                else:
                    rows.append((on_prop, f"exactly {card}"))
            if min_card:
                range_type = g.value(on_prop, RDFS.range) if on_prop else None
                if range_type:
                    range_name = get_qname(g, range_type, ns)
                    rows.append((on_prop, f"min {min_card} {range_name}"))
                else:
                    rows.append((on_prop, f"min {min_card}"))
            if max_card:
                range_type = g.value(on_prop, RDFS.range) if on_prop else None
                if range_type:
                    range_name = get_qname(g, range_type, ns)
                    rows.append((on_prop, f"max {max_card} {range_name}"))
                else:
                    rows.append((on_prop, f"max {max_card}"))
    return rows

def hyperlink_class(name: str, all_classes: set, ns: str):
    """Create a hyperlink only for classes in the local namespace."""
    if ':' not in name and name in all_classes:  # Local class without prefix
        return f"[{name}]({name}.md)"
    return name

def is_abstract(cls, g):
    abstract = g.value(cls, URIRef(ns + "abstract"))
    if abstract is None:
        abstract = g.value(cls, URIRef("http://protege.stanford.edu/ontologies/metadata#abstract"))
    return abstract is not None and str(abstract).lower() == "true"

def get_id(qname):
    if ':' in qname:
        prefix, local = qname.split(':', 1)
        return prefix + '_' + local
    return qname

def get_all_class_superclasses(cls, g):
    direct_supers = set()
    for super_cls in g.objects(cls, RDFS.subClassOf):
        if isinstance(super_cls, URIRef) and super_cls != OWL.Thing and (super_cls, RDF.type, OWL.Class) in g:
            direct_supers.add(super_cls)
    all_supers = set(direct_supers)
    for sup in direct_supers:
        all_supers.update(get_all_class_superclasses(sup, g))
    return all_supers

def is_refined_property(g: Graph, cls: URIRef, prop: URIRef, restriction: URIRef) -> bool:
    """Check if a property restriction in cls refines an inherited restriction."""
    all_supers = get_all_class_superclasses(cls, g)
    for super_cls in all_supers:
        for super_restr in g.objects(super_cls, RDFS.subClassOf):
            if (super_restr, RDF.type, OWL.Restriction) in g:
                super_prop = g.value(super_restr, OWL.onProperty)
                if super_prop == prop:
                    current_avf = g.value(restriction, OWL.allValuesFrom)
                    super_avf = g.value(super_restr, OWL.allValuesFrom)
                    current_card = g.value(restriction, OWL.qualifiedCardinality) or g.value(restriction, OWL.minQualifiedCardinality) or g.value(restriction, OWL.maxQualifiedCardinality)
                    super_card = g.value(super_restr, OWL.qualifiedCardinality) or g.value(super_restr, OWL.minQualifiedCardinality) or g.value(super_restr, OWL.maxQualifiedCardinality)
                    current_on_class = g.value(restriction, OWL.onClass)
                    super_on_class = g.value(super_restr, OWL.onClass)
                    if (current_avf != super_avf or
                        current_card != super_card or
                        current_on_class != super_on_class):
                        return True
    return False

def insert_spaces(name: str) -> str:
    name = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1 \2', name)
    name = re.sub(r'([a-z\d])([A-Z])', r'\1 \2', name)
    return name

# -------------------- namespaces --------------------
DESC_PROPS = (DC.description, SKOS.definition, RDFS.comment)
SKIP_IN_OTHER = set(DESC_PROPS) | {RDFS.label, DCTERMS.description, SKOS.note, SKOS.example}

# -------------------- main --------------------
def main():
    # Check if script is called without arguments
    if len(sys.argv) != 1:
        print("Usage: python ofn2mkdocs.py")
        sys.exit(1)

    # Check for mkdocs.yml in current directory
    root_dir = os.getcwd()
    mkdocs_path = os.path.join(root_dir, "mkdocs.yml")
    if not os.path.exists(mkdocs_path):
        print("Error: mkdocs.yml not found in current directory")
        sys.exit(1)

    # Check for docs directory
    docs_dir = os.path.join(root_dir, "docs")
    if not os.path.isdir(docs_dir):
        print("Error: docs directory not found")
        sys.exit(1)

    # Find all .ofn files in docs directory
    ofn_files = [os.path.join(docs_dir, f) for f in os.listdir(docs_dir) if f.lower().endswith('.ofn')]
    if not ofn_files:
        print("No .ofn files found in docs/")
        sys.exit(0)

    # Initialize global collections for patterns and classes
    global_patterns = {}
    global_all_classes = set()
    processed_count = 0
    errors = []
    abstract_map = {}
    ontology_info = {}  # Store dc:title, dcterms:description, and non-pattern classes for each OFN file

    # Process each OFN file
    for ofn_path in sorted(ofn_files):
        base_dir = docs_dir
        diagrams_dir = os.path.join(base_dir, "diagrams")
        classes_dir = os.path.join(base_dir, "classes")

        # Ensure output directories exist
        os.makedirs(diagrams_dir, exist_ok=True)
        os.makedirs(classes_dir, exist_ok=True)

        # Load OFN ontology and convert to RDF graph
        try:
            ont_doc = to_python(ofn_path)
            g = Graph()
            ont_doc.to_rdf(g)
            log.info("Loaded ontology %s with %d triples", ofn_path, len(g))
            if len(g) == 0:
                raise ValueError("RDF graph is empty after loading ontology")
        except Exception as e:
            errors.append(f"Failed to load or parse ontology from {ofn_path}: {str(e)}")
            log.error("Failed to load or parse ontology from %s: %s", ofn_path, str(e))
            continue

        # Dynamically set default namespace from ontology IRI
        global ns
        try:
            ns = str(ont_doc.ontology.iri) if ont_doc.ontology and ont_doc.ontology.iri else None
        except AttributeError:
            ns = None
        if not ns:
            log.warning("No ontology IRI found in OFN file %s; using example.com namespace", ofn_path)
            ns = "https://example.com/ontology#"
        log.info("Using default namespace for %s: %s", ofn_path, ns)

        # Extract ontology metadata
        dc_title = get_ontology_metadata(g, ns, DC.title) or "Untitled Ontology"
        dcterms_description = get_ontology_metadata(g, ns, DCTERMS.description) or ""
        if not dc_title:
            log.warning("No dc:title found for ontology in %s", ofn_path)
        if not dcterms_description:
            log.warning("No dcterms:description found for ontology in %s", ofn_path)
        ontology_info[ofn_path] = {
            "title": dc_title,
            "description": dcterms_description,
            "patterns": set(),
            "non_pattern_classes": set()
        }

        # Extract prefixes
        prefixes = get_prefix_named_pairs(ont_doc, ns)
        log.info("Prefixes for %s:", ofn_path)
        for d in prefixes:
            log.info("  %s → %s", d['prefix'], d['uri'])
        global prefix_map
        prefix_map = {_norm_base(d["uri"]): f"{d['prefix']}:" for d in prefixes}

        # Extract classes
        classes = set(g.subjects(RDF.type, OWL.Class)) - {OWL.Thing}
        log.info("Found %d classes in ontology %s", len(classes), ofn_path)
        if not classes:
            log.warning("No classes found with RDF.type OWL.Class in %s", ofn_path)
        else:
            log.info("Classes found in %s: %s", ofn_path, [str(cls) for cls in classes])

        # Filter classes by namespace
        local_classes = [cls for cls in classes if str(cls).startswith(ns)]
        log.info("Filtered to %d local classes in namespace %s for %s", len(local_classes), ns, ofn_path)
        if local_classes:
            log.info("Local classes in %s: %s", ofn_path, [get_qname(g, cls, ns) for cls in local_classes])

        # Update abstract_map
        for cls in classes:
            cls_qname = get_qname(g, cls, ns)
            abstract_map[cls_qname] = is_abstract(cls, g)

        # Collect patterns and all classes
        for cls in local_classes:
            cls_name = get_label(g, cls)
            global_all_classes.add(cls_name)
            pattern_literal = g.value(cls, XSD.pattern)
            if pattern_literal and isinstance(pattern_literal, Literal):
                pattern_name = str(pattern_literal)
                if pattern_name not in global_patterns:
                    global_patterns[pattern_name] = {"classes": []}
                global_patterns[pattern_name]["classes"].append(cls_name)
                ontology_info[ofn_path]["patterns"].add(pattern_name)
            else:
                ontology_info[ofn_path]["non_pattern_classes"].add(cls_name)

        def fmt_title(name: str) -> str:
            return f"<B><I>{name}</I></B>" if abstract_map.get(name, False) else f"<B>{name}</B>"

        # Process classes for diagrams and Markdown
        for cls in sorted(local_classes, key=lambda u: get_label(g, u).lower()):
            cls_name = get_label(g, cls)
            cls_id = get_id(cls_name)
            log.info("Processing class: %s from %s", cls_name, ofn_path)

            try:
                # --- Diagram Generation ---
                dot = Digraph(
                    comment=f"Diagram for {cls_name}",
                    format="svg",
                    graph_attr={"overlap": "false", "splines": "true", "rankdir": "TB"},
                    node_attr={"shape": "none", "fontsize": "12", "fontname": "Arial", "margin": "0"},
                    edge_attr={"fontsize": "10", "fontname": "Arial"}
                )

                # Define superclasses (only direct superclasses via rdfs:subClassOf)
                superclasses = set()
                for super_cls in g.objects(cls, RDFS.subClassOf):
                    if isinstance(super_cls, URIRef) and super_cls != OWL.Thing and (super_cls, RDF.type, OWL.Class) in g:
                        superclasses.add(get_qname(g, super_cls, ns))
                for sup in sorted(superclasses):
                    sup_id = get_id(sup)
                    dot.node(sup_id, label=f'<<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="1"><TR><TD BGCOLOR="lightgray" ALIGN="CENTER">{fmt_title(sup)}</TD></TR></TABLE>>', margin="0")

                # Define main class with direct or refined datatype properties
                with dot.subgraph() as main_group:
                    main_group.attr(rank='max')
                    data_props = defaultdict(list)
                    # Collect direct or refined datatype properties
                    for restriction in g.objects(cls, RDFS.subClassOf):
                        if (restriction, RDF.type, OWL.Restriction) in g:
                            prop = g.value(restriction, OWL.onProperty)
                            if prop and (prop, RDF.type, OWL.DatatypeProperty) in g:
                                prop_name = get_qname(g, prop, ns)
                                range_type = g.value(prop, RDFS.range) or XSD.string
                                range_name = get_qname(g, range_type, ns).split(":")[-1]
                                qualified_card = g.value(restriction, OWL.qualifiedCardinality)
                                min_qualified_card = g.value(restriction, OWL.minQualifiedCardinality)
                                max_qualified_card = g.value(restriction, OWL.maxQualifiedCardinality)
                                all_values_from = g.value(restriction, OWL.allValuesFrom)
                                restrictions = []
                                if qualified_card:
                                    restrictions.append(f"exactly {qualified_card}")
                                if min_qualified_card:
                                    restrictions.append(f"min {min_qualified_card}")
                                if max_qualified_card:
                                    restrictions.append(f"max {max_qualified_card}")
                                if all_values_from:
                                    restrictions.append("only")
                                restriction_str = f"«{', '.join(restrictions)}»" if restrictions else ""
                                # Check if this is a refined property
                                stereotype = "refined" if is_refined_property(g, cls, prop, restriction) else ""
                                data_props[prop_name].append((restriction_str, range_name, stereotype))

                    attributes = []
                    for prop_name, restrictions in sorted(data_props.items()):
                        all_restrictions = []
                        range_names = set()
                        stereotypes = set()
                        for restriction_str, range_name, stereotype in restrictions:
                            if restriction_str:
                                all_restrictions.append(restriction_str.strip())
                            range_names.add(range_name)
                            if stereotype:
                                stereotypes.add(stereotype)
                        range_name = range_names.pop() if range_names else "string"
                        restriction_label = ", ".join(sorted(set(r.strip('«»') for r in all_restrictions))) if all_restrictions else ""
                        stereotype_label = ", ".join(sorted(set(s for s in stereotypes))) if stereotypes else ""
                        attribute = f"{prop_name}: {range_name}"
                        if restriction_label or stereotype_label:
                            labels = [l for l in [restriction_label, stereotype_label] if l]
                            attribute = f"{attribute} «{', '.join(labels)}»"
                        attributes.append(attribute)

                    attributes_html = "".join(f'<TR><TD ALIGN="LEFT">{prop}</TD></TR>' for prop in attributes)
                    main_label = f'<<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="1"><TR><TD BGCOLOR="lightgray" ALIGN="CENTER" PORT="e">{fmt_title(cls_name)}</TD></TR>{attributes_html}</TABLE>>'
                    main_group.node(cls_id, main_label, margin="0")

                # Define associated classes (via object properties)
                associated_classes = set()
                for restriction in g.objects(cls, RDFS.subClassOf):
                    if (restriction, RDF.type, OWL.Restriction) in g:
                        prop = g.value(restriction, OWL.onProperty)
                        if prop and (prop, RDF.type, OWL.ObjectProperty) in g:
                            on_class = g.value(restriction, OWL.onClass)
                            if on_class and on_class != OWL.Thing:
                                on_class_qn = get_qname(g, on_class, ns)
                                if on_class_qn != cls_name and on_class_qn not in superclasses:
                                    associated_classes.add(on_class_qn)
                            all_values_from = g.value(restriction, OWL.allValuesFrom)
                            if all_values_from and all_values_from != OWL.Thing:
                                union_collection = g.value(all_values_from, OWL.unionOf)
                                if union_collection and union_collection != RDF.nil:
                                    current = union_collection
                                    while current != RDF.nil:
                                        first = g.value(current, RDF.first)
                                        if first and first != OWL.Thing:
                                            member_qn = get_qname(g, first, ns)
                                            if member_qn != cls_name and member_qn not in superclasses:
                                                associated_classes.add(member_qn)
                                        current = g.value(current, RDF.rest)
                                else:
                                    all_qn = get_qname(g, all_values_from, ns)
                                    if all_qn != cls_name and all_qn not in superclasses:
                                        associated_classes.add(all_qn)
                            some_values_from = g.value(restriction, OWL.someValuesFrom)
                            if some_values_from and some_values_from != OWL.Thing:
                                some_qn = get_qname(g, some_values_from, ns)
                                if some_qn != cls_name and some_qn not in superclasses:
                                    associated_classes.add(some_qn)

                with dot.subgraph(name='cluster_associated') as associated_cluster:
                    associated_cluster.attr(style='invis', label='')
                    associated_cluster.node('Invis', label='<<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="0" CELLPADDING="1"><TR><TD></TD></TR></TABLE>>', style='invis', margin="0")
                    for assoc in sorted(associated_classes):
                        assoc_id = get_id(assoc)
                        attrs = dict(
                            label=f'<<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="1">'
                                f'<TR><TD BGCOLOR="lightgray" ALIGN="CENTER">{fmt_title(assoc)}</TD></TR>'
                                f'</TABLE>>',
                            margin="0"
                        )

                        # Only add URL if there’s no colon
                        if ":" not in assoc:
                            attrs["URL"] = f"../classes/{assoc}"
                            attrs["tooltip"] = assoc

                        associated_cluster.node(assoc_id, **attrs)

                # Define generalization relationships
                for sup in sorted(superclasses):
                    sup_id = get_id(sup)
                    dot.edge(cls_id, sup_id, arrowhead="onormal", style="solid")

                # Define invisible association
                if associated_classes:
                    dot.edge(cls_id, 'Invis', style="invis")

                # Define object property associations (direct or refined)
                combined = defaultdict(dict)
                log.debug("Processing object property restrictions for %s:", cls_name)
                for restriction in g.objects(cls, RDFS.subClassOf):
                    if (restriction, RDF.type, OWL.Restriction) in g:
                        prop = g.value(restriction, OWL.onProperty)
                        if prop and (prop, RDF.type, OWL.ObjectProperty) in g:
                            prop_name = get_qname(g, prop, ns)
                            is_refined = is_refined_property(g, cls, prop, restriction)
                            style = "dashed" if is_refined else "solid"
                            target_id = None
                            label_part = None
                            is_union = False
                            union_members = None
                            reflexive = False
                            target_qname = None

                            qualified_card = g.value(restriction, OWL.qualifiedCardinality)
                            min_qualified_card = g.value(restriction, OWL.minQualifiedCardinality)
                            max_qualified_card = g.value(restriction, OWL.maxQualifiedCardinality)
                            on_class = g.value(restriction, OWL.onClass)
                            if (qualified_card or min_qualified_card or max_qualified_card) and on_class:
                                target_qname = get_qname(g, on_class, ns)
                                target_id = get_id(target_qname)
                                if qualified_card:
                                    label_part = f"exactly {qualified_card}"
                                elif min_qualified_card:
                                    label_part = f"min {min_qualified_card}"
                                elif max_qualified_card:
                                    label_part = f"max {max_qualified_card}"
                                reflexive = target_qname == cls_name
                                log.debug("  - Property: %s, Target: %s, Cardinality: %s, Reflexive: %s, Refined: %s", prop_name, target_qname, label_part, reflexive, is_refined)

                            all_values_from = g.value(restriction, OWL.allValuesFrom)
                            if all_values_from:
                                target = all_values_from
                                union_collection = g.value(target, OWL.unionOf)
                                if union_collection and union_collection != RDF.nil:
                                    members = []
                                    current = union_collection
                                    while current != RDF.nil:
                                        first = g.value(current, RDF.first)
                                        if first:
                                            members.append(get_qname(g, first, ns))
                                        current = g.value(current, RDF.rest)
                                    if members:
                                        members.sort()
                                        union_id = f"Union_{get_id(prop_name)}"
                                        target_id = union_id
                                        is_union = True
                                        union_members = members
                                else:
                                    target_qname = get_qname(g, target, ns)
                                    target_id = get_id(target_qname)
                                    reflexive = target_qname == cls_name
                                label_part = "only"
                                log.debug("  - Property: %s, Target: %s, Restriction: only, Reflexive: %s, Refined: %s", prop_name, target_qname, reflexive, is_refined)

                            some_values_from = g.value(restriction, OWL.someValuesFrom)
                            if some_values_from:
                                target = some_values_from
                                target_qname = get_qname(g, target, ns)
                                target_id = get_id(target_qname)
                                reflexive = target_qname == cls_name
                                label_part = "some"
                                log.debug("  - Property: %s, Target: %s, Restriction: some, Reflexive: %s, Refined: %s", prop_name, target_qname, reflexive, is_refined)

                            if target_id and label_part:
                                key = (prop_name, target_id)
                                if key not in combined:
                                    combined[key] = {
                                        'label_parts': [],
                                        'style': style,
                                        'prop_name': prop_name,
                                        'target_id': target_id,
                                        'is_union': is_union,
                                        'union_members': union_members,
                                        'reflexive': reflexive,
                                        'target_qname': target_qname
                                    }
                                combined[key]['label_parts'].append(label_part)

                prev = 'Invis'
                for assoc in sorted(associated_classes):
                    assoc_id = get_id(assoc)
                    dot.edge(prev, assoc_id, style="invis")
                    prev = assoc_id

                for key, data in combined.items():
                    prop_name = data['prop_name']
                    target_id = data['target_id']
                    style = data['style']
                    label_parts = data['label_parts']
                    is_union = data['is_union']
                    union_members = data['union_members']
                    reflexive = data['reflexive']
                    target_qname = data['target_qname']
                    label_prefix = f"«{', '.join(sorted(set(label_parts)))}» " if label_parts else ""
                    if style == "solid":
                        label = label_prefix + prop_name
                    else:
                        label = label_prefix + "onProperty: " + prop_name
                    log.debug("  - Adding edge: %s -> %s, Label: %s, Style: %s, Reflexive: %s", cls_name, target_qname, label, style, reflexive)
                    if is_union:
                        union_id = target_id
                        union_label = f'«unionOf»<BR/>[{ " or ".join(union_members) }]'
                        dot.node(union_id, f'<<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="1" BGCOLOR="lightyellow"><TR><TD ALIGN="CENTER">{union_label}</TD></TR></TABLE>>', margin="0")
                        for member in union_members:
                            assoc_id = get_id(member)
                            associated_classes.add(member)
                            dot.node(assoc_id, label=f'<<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="1"><TR><TD BGCOLOR="lightgray" ALIGN="CENTER">{fmt_title(member)}</TD></TR></TABLE>>', margin="0")
                            dot.edge(union_id, assoc_id, style="dotted", label="member", arrowhead="normal")
                    if reflexive:
                        dot.edge(cls_id, cls_id, label=label, style=style, arrowhead="normal")
                    else:
                        dot.edge(cls_id, target_id, label=label, style=style, arrowhead="normal")

                # Save DOT file and render SVG/PNG
                log.info("Saving diagram for %s", cls_name)
                dot_file = os.path.join(diagrams_dir, f"{cls_name}")
                dot.save(dot_file)
                dot.render(dot_file, cleanup=False)
                dot.render(dot_file, format='png', cleanup=False)

                # --- Markdown Generation ---
                filename = os.path.join(classes_dir, f"{cls_name}.md")
                log.debug("Writing %s", filename)

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
                    restr_rows = class_restrictions(g, cls)
                    formalization_md = ""
                    if restr_rows:
                        formalization_md += "## Formalization\n\n"
                        formalization_md += "| Property | Value Restriction |\n"
                        formalization_md += "|----------|-------------------|\n"
                        for prop, restr in sorted(restr_rows):
                            # Hyperlink local classes (no prefix)
                            restr_hlinked = re.sub(r'\b([A-Z][a-zA-Z0-9]*)\b', lambda m: hyperlink_class(m.group(0), global_all_classes, ns) if m.group(0) not in ['or', 'exactly', 'min', 'max'] else m.group(0), restr)
                            formalization_md += f"| {prop} | {restr_hlinked} |\n"
                        formalization_md += "\n"
                    other_ann = list(iter_annotations(g, cls))
                    other_md = ""
                    if other_ann:
                        other_md += "## Other Annotations\n\n"
                        for p, v in sorted(other_ann):
                            if p == 'xsd:pattern':
                                v = hyperlink_class(v, global_all_classes, ns)
                            other_md += f"- **{p}**: {v}\n"
                        other_md += "\n"
                    content = title + top_desc + note_md + example_md + diagram_line + formalization_md + other_md

                # Write the Markdown file
                with open(filename, "w", encoding="utf-8") as f:
                    f.write(content)

                processed_count += 1

            except Exception as e:
                errors.append(f"Error processing class {cls_name} from {ofn_path}: {str(e)}")
                log.error("Error processing class %s from %s: %s", cls_name, ofn_path, str(e))

    # Update mkdocs.yml nav section
    with open(mkdocs_path, 'r', encoding="utf-8") as f:
        config = yaml.load(f, Loader=SafeMkDocsLoader)
    
    # Create navigation structure
    new_nav = [{"Home": "index.md"}]
    pattern_names = set(global_patterns.keys())
    for pat_cls_name in sorted(pattern_names, key=str.lower):
        display_pat = insert_spaces(pat_cls_name)
        sub_nav = [{"Overview": f"classes/{pat_cls_name}.md"}]
        member_classes = sorted(global_patterns[pat_cls_name]["classes"], key=str.lower)
        for mem_cls_name in member_classes:
            display_mem = insert_spaces(mem_cls_name)
            sub_nav.append({display_mem: f"classes/{mem_cls_name}.md"})
        new_nav.append({display_pat: sub_nav})
    
    # Add non-pattern classes (classes without xsd:pattern annotation and not pattern classes)
    pattern_members = set(sum([data["classes"] for data in global_patterns.values()], []))
    non_pattern_classes = sorted(global_all_classes - pattern_members - pattern_names, key=str.lower)
    for cls_name in non_pattern_classes:
        display_cls = insert_spaces(cls_name)
        new_nav.append({display_cls: f"classes/{cls_name}.md"})

    config["nav"] = new_nav
    with open(mkdocs_path, 'w', encoding="utf-8") as f:
        yaml.safe_dump(config, f, sort_keys=False, allow_unicode=True)

    # Generate index.md
    index_path = os.path.join(docs_dir, "index.md")
    index_content = ""
    
    if len(ofn_files) == 1:
        # Single OFN file case
        ofn_path = ofn_files[0]
        ofn_filename = os.path.basename(ofn_path)
        title = ontology_info[ofn_path]["title"]
        description = ontology_info[ofn_path]["description"]
        patterns = sorted(ontology_info[ofn_path]["patterns"], key=str.lower)
        non_pattern_classes = sorted(ontology_info[ofn_path]["non_pattern_classes"] - pattern_names, key=str.lower)
        
        index_content += f"# {title}\n\n"
        if description:
            index_content += f"{description}\n\n"
        index_content += "This ontology consists of the following patterns:\n\n"
        for pat_name in patterns:
            display_pat = insert_spaces(pat_name)
            index_content += f"- [{display_pat}](classes/{pat_name}.md)\n"
        if non_pattern_classes:
            index_content += "\nThe ontology also contains the following classes that are not assigned to any pattern:\n\n"
            for cls_name in non_pattern_classes:
                display_cls = insert_spaces(cls_name)
                index_content += f"- [{display_cls}](classes/{cls_name}.md)\n"
        index_content += f"\nThe formal definition of these patterns is available in [OWL Functional Notation]({ofn_filename}).\n"
    else:
        # Multiple OFN files case
        readme_path = os.path.join(root_dir, "README.md")
        if os.path.exists(readme_path):
            with open(readme_path, 'r', encoding="utf-8") as f:
                first_line = f.readline().strip()
                title = first_line.lstrip('#').strip() if first_line.startswith('#') else first_line
        else:
            title = "No README.md file found for title"
        
        index_content += f"# {title}\n\n"
        for ofn_path in sorted(ofn_files):
            ofn_filename = os.path.basename(ofn_path)
            title = ontology_info[ofn_path]["title"]
            description = ontology_info[ofn_path]["description"]
            patterns = sorted(ontology_info[ofn_path]["patterns"], key=str.lower)
            non_pattern_classes = sorted(ontology_info[ofn_path]["non_pattern_classes"] - pattern_names, key=str.lower)
            
            index_content += f"## {title}\n\n"
            if description:
                index_content += f"{description}\n\n"
            index_content += "This ontology consists of the following patterns:\n\n"
            for pat_name in patterns:
                display_pat = insert_spaces(pat_name)
                index_content += f"- [{display_pat}](classes/{pat_name}.md)\n"
            if non_pattern_classes:
                index_content += "\nThe ontology also contains the following classes that are not assigned to any pattern:\n\n"
                for cls_name in non_pattern_classes:
                    display_cls = insert_spaces(cls_name)
                    index_content += f"- [{display_cls}](classes/{cls_name}.md)\n"
            index_content += f"\nThe formal definition of these patterns is available in [OWL Functional Notation]({ofn_filename}).\n\n"

    # Write index.md
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(index_content)
    log.info("Generated index.md at %s", index_path)

    log.info("Total processed classes: %d", processed_count)
    if errors:
        log.error("Errors occurred:")
        for err in errors:
            log.error(err)

if __name__ == "__main__":
    main()