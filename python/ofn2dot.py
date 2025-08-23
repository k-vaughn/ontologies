from rdflib import Graph, RDF, RDFS, OWL, XSD
from graphviz import Digraph
import os
from funowl.converters.functional_converter import to_python
from collections import defaultdict
from rdflib import URIRef, Literal, XSD, Namespace
# import logging

# Set up logging
# logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
# logger = logging.getLogger(__name__)

# Namespaces
ns = "https://isotc204.org/25965/transport/transportnetwork#"
protege_ns = Namespace("http://protege.stanford.edu/ontologies/metadata#")
rdf_ns = Namespace("http://www.w3.org/1999/02/22-rdf-syntax-ns#")
owl_ns = Namespace("http://www.w3.org/2002/07/owl#")
dcterms_ns = Namespace("http://purl.org/dc/terms/")


# Ensure output directory exists
os.makedirs("diagrams", exist_ok=True)

# Load OFN ontology and convert to RDF graph
ont_doc = to_python("transportnetwork.ofn")
g = Graph()
ont_doc.to_rdf(g)

# Namespaces
ns = "https://isotc204.org/25965/transport/transportnetwork#"
skos_ns = "http://www.w3.org/2004/02/skos/core#"

# Custom prefixes
prefixes = {
    "https://isotc204.org/25965/transport/transportnetwork#": "",
    "https://www.opengis.net/ont/geosparql#": "geo:",
    "https://www.w3.org/2006/time#": "time:",
    "https://standards.iso.org/iso-iec/5087/-2/ed-1/en/ontology/code#": "code:",
    "https://standards.iso.org/iso-iec/5087/-1/ed-1/en/ontology/GenericProperties#": "genProp:",
    "https://standards.iso.org/iso-iec/5087/-1/ed-1/en/ontology/Mereology#": "partwhole:",
    "https://standards.iso.org/iso-iec/5087/-2/ed-1/en/ontology/transinfras#": "transinfras:",
    "https://isotc204.org/25965/transport/travelcorridor#": "travelcorridor:",
    "http://www.w3.org/2004/02/skos/core#": "skos:",
    "http://www.w3.org/2001/XMLSchema#": "xsd:",
    "https://standards.iso.org/iso-iec/5087/-2/ed-1/en/ontology/activity#": "activity:",
    "https://standards.iso.org/iso-iec/5087/-1/ed-1/en/ontology/CityUnits#": "cityunits:",
}

def get_qname(uri):
    str_uri = str(uri)
    # Normalize URI by removing trailing slash for matching
    normalized_uri = str_uri.rstrip('/')
    for base, pref in prefixes.items():
        # Normalize base URI for comparison
        normalized_base = base.rstrip('/')
        if normalized_uri.startswith(normalized_base):
            local_name = normalized_uri[len(normalized_base):]
            # Ensure local name doesn't start with a slash
            if local_name.startswith('/'):
                local_name = local_name[1:]
            return pref + local_name
    # Debug: Log URIs that don't match any prefix
    print(f"Warning: No prefix found for URI: {str_uri}")
    return str_uri

def is_abstract(cls, g):
    """Check if the class has protege:abstract annotation set to true."""
    abstract = g.value(cls, protege_ns.abstract)
    return abstract is not None and str(abstract).lower() == "true"

def get_id(qname):
    if ':' in qname:
        prefix, local = qname.split(':', 1)
        return prefix + '_' + local
    else:
        return qname

def get_all_class_superclasses(cls, g):
    direct_supers = set()
    for super_cls in g.objects(cls, RDFS.subClassOf):
        if (super_cls, RDF.type, OWL.Class) in g or str(super_cls).startswith(ns):
            direct_supers.add(super_cls)
    all_supers = set(direct_supers)
    for sup in direct_supers:
        all_supers.update(get_all_class_superclasses(sup, g))
    return all_supers

# Extract classes
classes = set(g.subjects(RDF.type, OWL.Class)) - {OWL.Thing}

processed_count = 0
errors = []
# Build abstract_map: keys are class names, values are is_abstract(cls, g)
abstract_map = {}

for cls in classes:
    cls_str = str(cls)
    if cls_str.startswith(ns):
        cls_name = get_qname(cls)
        abstract_map[cls_name] = is_abstract(cls, g)
        # logger.debug(f"Added {cls_name} to map with abstract status: {abstract_map[cls_name]}")
    else:
        continue  # Skip non-local classes

for name, is_abs in abstract_map.items():
    print(f"{name}: {is_abs}")

# Helper function for formatting class titles
def fmt_title(name: str) -> str:
    """Return HTML-like label for a class name: bold; italic if abstract."""
    return f"<B><I>{name}</I></B>" if abstract_map.get(name, False) else f"<B>{name}</B>"

# --- New helper and constant for skipping ITSPatternThing and its subclasses ---
from rdflib import URIRef

PATTERN_CLS = URIRef(ns + "ITSPatternThing")
def is_descendant_of(cls_uri, ancestor_uri, g):
    """Return True if cls_uri == ancestor_uri or cls_uri is a (transitive) subclass of ancestor_uri."""
    if cls_uri == ancestor_uri:
        return True
    # Reuse existing superclass traversal
    try:
        return ancestor_uri in get_all_class_superclasses(cls_uri, g)
    except RecursionError:
        return False

for cls in classes:    # Extract class name
    cls_str = str(cls)
    if cls_str.startswith(ns):
        # Skip ITSPatternThing and any of its subclasses from output generation
        if is_descendant_of(cls, PATTERN_CLS, g):
            continue
        cls_name = get_qname(cls)
        cls_id = get_id(cls_name)
    else:
        continue  # Skip non-local classes

    try:
        print(f"Processing class: {cls_name}")

        # Initialize Graphviz diagram
        dot = Digraph(
            comment=f"Diagram for {cls_name}",
            format="svg",
            graph_attr={"overlap": "false", "splines": "true", "rankdir": "TB"},
            node_attr={"shape": "none", "fontsize": "12", "fontname": "Arial", "margin": "0"},
            edge_attr={"fontsize": "10", "fontname": "Arial"}
        )

        # 1. Define graph, node, edge, rankdir items (already set in Digraph with rankdir=TB)

        # 2. Define super classes
        superclasses = set()
        for super_cls in g.objects(cls, RDFS.subClassOf):
            if (super_cls, RDF.type, OWL.Class) in g or str(super_cls).startswith(ns):
                superclasses.add(get_qname(super_cls))
        for sup in sorted(superclasses):
            sup_id = get_id(sup)
            dot.node(sup_id, label=f'<<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="1"><TR><TD BGCOLOR="lightgray" ALIGN="CENTER">{fmt_title(sup)}</TD></TR></TABLE>>', margin="0")
            
        # 3. Define the main class in its own group with a rank=max statement
        with dot.subgraph() as main_group:
            main_group.attr(rank='max')
            # Collect data properties
            data_props = defaultdict(list)
            for restriction in g.objects(cls, RDFS.subClassOf):
                if (restriction, RDF.type, OWL.Restriction) in g:
                    prop = g.value(restriction, OWL.onProperty)
                    if prop and (prop, RDF.type, OWL.DatatypeProperty) in g:
                        prop_name = get_qname(prop)
                        range_type = g.value(prop, RDFS.range) or XSD.string
                        range_name = get_qname(range_type).split(":")[-1]
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
                        if restrictions:
                            restriction_str = f"«{', '.join(restrictions)}»"
                        else:
                            restriction_str = ""
                        data_props[prop_name].append((restriction_str, range_name))

            # Combine restrictions for each property
            attributes = []
            for prop_name, restrictions in sorted(data_props.items()):
                # Combine all restrictions for this property
                all_restrictions = []
                range_names = set()
                for restriction_str, range_name in restrictions:
                    if restriction_str:
                        all_restrictions.append(restriction_str.strip())
                    range_names.add(range_name)
                range_name = range_names.pop() if range_names else "string"
                restriction_label = ", ".join(sorted(set(r.strip('«»') for r in all_restrictions))) if all_restrictions else ""
                attribute = f"{prop_name}: {range_name}"
                if restriction_label:
                    attribute = f"{attribute} «{restriction_label}»"
                attributes.append(attribute)

            attributes_html = "".join(f'<TR><TD ALIGN="LEFT">{prop}</TD></TR>' for prop in attributes)
            main_label = f'<<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="1"><TR><TD BGCOLOR="lightgray" ALIGN="CENTER" PORT="e">{fmt_title(cls_name)}</TD></TR>{attributes_html}</TABLE>>'
            main_group.node(cls_id, main_label, margin="0")

        # 4. Define subgraph containing all of the associated classes with an invisible box
        associated_classes = set()
        # Collect associated classes from object property restrictions
        for restriction in g.objects(cls, RDFS.subClassOf):
            if (restriction, RDF.type, OWL.Restriction) in g:
                prop = g.value(restriction, OWL.onProperty)
                if prop and (prop, RDF.type, OWL.ObjectProperty) in g:
                    on_class = g.value(restriction, OWL.onClass)
                    if on_class:
                        on_class_qn = get_qname(on_class)
                        if on_class_qn != cls_name:
                            associated_classes.add(on_class_qn)
                    all_values_from = g.value(restriction, OWL.allValuesFrom)
                    if all_values_from:
                        union_collection = g.value(all_values_from, OWL.unionOf)
                        if union_collection and union_collection != RDF.nil:
                            current = union_collection
                            while current != RDF.nil:
                                first = g.value(current, RDF.first)
                                if first:
                                    member_qn = get_qname(first)
                                    if member_qn != cls_name:
                                        associated_classes.add(member_qn)
                                current = g.value(current, RDF.rest)
                        else:
                            all_qn = get_qname(all_values_from)
                            if all_qn != cls_name:
                                associated_classes.add(all_qn)
                    some_values_from = g.value(restriction, OWL.someValuesFrom)
                    if some_values_from:
                        some_qn = get_qname(some_values_from)
                        if some_qn != cls_name:
                            associated_classes.add(some_qn)

        with dot.subgraph(name='cluster_associated') as associated_cluster:
            associated_cluster.attr(style='invis', label='')

            # 5. Include an Invis class as the first item in this group
            associated_cluster.node('Invis', label='<<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="0" CELLPADDING="1"><TR><TD></TD></TR></TABLE>>', style='invis', margin="0")

            # 6. Add associated classes
            for assoc in sorted(associated_classes):
                assoc_id = get_id(assoc)
                associated_cluster.node(assoc_id, label=f'<<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="1"><TR><TD BGCOLOR="lightgray" ALIGN="CENTER">{fmt_title(assoc)}</TD></TR></TABLE>>', margin="0")
                
        # 7. Define the generalization relationships
        for sup in sorted(superclasses):
            sup_id = get_id(sup)
            dot.edge(cls_id, sup_id, arrowhead="onormal", style="solid")

        # 8. Define the association to the Invis class (invisible)
        dot.edge(cls_id, 'Invis', style="invis")

        # 9. Define all other associations per the OFN file
        combined = defaultdict(dict)
        for restriction in g.objects(cls, RDFS.subClassOf):
            if (restriction, RDF.type, OWL.Restriction) in g:
                prop = g.value(restriction, OWL.onProperty)
                if prop and (prop, RDF.type, OWL.ObjectProperty) in g:
                    prop_name = get_qname(prop)
                    is_inherited = any(
                        any(
                            g.value(rest, OWL.onProperty) == prop
                            for rest in g.objects(sup, RDFS.subClassOf)
                            if (rest, RDF.type, OWL.Restriction) in g
                        )
                        for sup in get_all_class_superclasses(cls, g)
                    )
                    style = "dashed" if is_inherited else "solid"

                    target_id = None
                    label_part = None
                    is_union = False
                    union_members = None
                    reflexive = False
                    target_qname = None

                    # Handle cardinalities
                    qualified_card = g.value(restriction, OWL.qualifiedCardinality)
                    min_qualified_card = g.value(restriction, OWL.minQualifiedCardinality)
                    max_qualified_card = g.value(restriction, OWL.maxQualifiedCardinality)
                    on_class = g.value(restriction, OWL.onClass)
                    if (qualified_card or min_qualified_card or max_qualified_card) and on_class:
                        target_qname = get_qname(on_class)
                        target_id = get_id(target_qname)
                        if qualified_card:
                            label_part = f"exactly {qualified_card}"
                        elif min_qualified_card:
                            label_part = f"min {min_qualified_card}"
                        elif max_qualified_card:
                            label_part = f"max {max_qualified_card}"

                    # Handle universal (allValuesFrom -> only)
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
                                    members.append(get_qname(first))
                                current = g.value(current, RDF.rest)
                            if members:
                                members.sort()
                                union_id = f"Union_{get_id(prop_name)}"
                                target_id = union_id
                                is_union = True
                                union_members = members
                        else:
                            target_qname = get_qname(target)
                            if target_qname != cls_name:
                                target_id = get_id(target_qname)
                            else:
                                reflexive = True
                                target_id = cls_id
                        label_part = "only"

                    # Handle existential (someValuesFrom)
                    some_values_from = g.value(restriction, OWL.someValuesFrom)
                    if some_values_from:
                        target = some_values_from
                        target_qname = get_qname(target)
                        if target_qname != cls_name:
                            target_id = get_id(target_qname)
                        else:
                            reflexive = True
                            target_id = cls_id
                        label_part = "some"

                    if target_id is not None and label_part is not None:
                        key = (prop_name, target_id)
                        if 'label_parts' not in combined[key]:
                            combined[key]['label_parts'] = []
                        combined[key]['label_parts'].append(label_part)
                        combined[key]['style'] = style
                        combined[key]['prop_name'] = prop_name
                        combined[key]['target_id'] = target_id
                        combined[key]['is_union'] = is_union
                        combined[key]['union_members'] = union_members if is_union else None
                        combined[key]['reflexive'] = reflexive
                        combined[key]['target_qname'] = target_qname

        # 10. Define a sequence of associations that traverse all of the classes (hidden)
        prev = 'Invis'
        for assoc in sorted(associated_classes):
            assoc_id = get_id(assoc)
            dot.edge(prev, assoc_id, style="invis")
            prev = assoc_id

        # Add associations from combined
        for key, data in combined.items():
            prop_name = data['prop_name']
            target_id = data['target_id']
            style = data['style']
            label_parts = data['label_parts']
            label = f"«{', '.join(sorted(label_parts))}»\\nonProperty: {prop_name}" if label_parts else f"onProperty: {prop_name}"
            if data['is_union']:
                union_id = target_id
                members = data['union_members']
                union_label = f'«unionOf»<BR/>[{ " or ".join(members) }]'
                dot.node(union_id, f'<<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="1" BGCOLOR="lightyellow"><TR><TD ALIGN="CENTER">{union_label}</TD></TR></TABLE>>', margin="0")
                for member in members:
                    assoc_id = get_id(member)
                    dot.edge(union_id, assoc_id, style="dotted", label="member", arrowhead="normal")
            if data['reflexive']:
                dot.edge(cls_id, cls_id, label=label, style=style)
            else:
                dot.edge(cls_id, target_id, label=label, style=style, arrowhead="normal")

        # Save DOT file and render SVG
        dot_file = f"diagrams/{cls_name}.dot"
        stem = f"diagrams/{cls_name}"
        dot.save(dot_file)
        dot.render(stem, cleanup=False)
        # Render PNG
        dot.render(stem, format='png', cleanup=False)

        processed_count += 1

    except Exception as e:
        errors.append(f"Error processing {cls_name}: {str(e)}")

print(f"Total processed classes: {processed_count}")
if errors:
    print("Errors occurred:")
    for err in errors:
        print(err)