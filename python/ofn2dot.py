import os
import re
from rdflib import Graph, RDF, RDFS, OWL, XSD, URIRef
from graphviz import Digraph
from collections.abc import Mapping
from funowl.converters.functional_converter import to_python
from collections import defaultdict

def get_prefix_named_pairs(ontology_doc):
    """Return [{'prefix': <str>, 'uri': <str>}, ...] from funowl PrefixDeclarations,
    handling different return shapes of as_prefixes() across funowl versions."""
    pd = getattr(ontology_doc, "prefixDeclarations", None)
    if not pd:
        return []

    ap = pd.as_prefixes()  # may be mapping-like, a view, or a list of objects/tuples

    # Case 1: mapping-like (has .items())
    if hasattr(ap, "items"):
        return [{"prefix": str(k), "uri": str(v)} for k, v in ap.items()]

    # Case 2: iterable (list/tuple) of pairs or objects
    out = []
    out.append({"prefix": "", "uri": ns})
    for item in ap:
        if isinstance(item, tuple) and len(item) == 2:
            k, v = item
            out.append({"prefix": str(k), "uri": str(v)})
            print("      ", k, v)
            continue

        # funowl Prefix object variants
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
    return out

def _norm_base(u: str) -> str:
    return u.rstrip('/#')

# Determine paths relative to the script location
script_dir = os.path.dirname(__file__)
ofn_path = os.path.join(script_dir, "..", "docs", "metr.ofn")
diagrams_dir = os.path.join(script_dir, "..", "docs", "diagrams")

# Check if OFN file exists
if not os.path.exists(ofn_path):
    raise FileNotFoundError(f"OFN file not found at {ofn_path}")

# Ensure output directory exists
os.makedirs(diagrams_dir, exist_ok=True)

# Load OFN ontology and convert to RDF graph
try:
    ont_doc = to_python(ofn_path)
    g = Graph()
    ont_doc.to_rdf(g)
    print(f"Loaded ontology with {len(g)} triples")
    if len(g) == 0:
        raise ValueError("RDF graph is empty after loading ontology")
except Exception as e:
    raise RuntimeError(f"Failed to load or parse ontology from {ofn_path}: {str(e)}")

# Dynamically set default namespace from ontology IRI
try:
    ns = str(ont_doc.ontology.iri) if ont_doc.ontology and ont_doc.ontology.iri else None
except AttributeError:
    ns = None
if not ns:
    print("Warning: No ontology IRI found in OFN file; using fallback namespace")
    ns = "https://isotc204.org/ontologies/ofn/transport/metr"
print(f"Using default namespace: {ns}")

# Extract prefixes
prefixes = get_prefix_named_pairs(ont_doc)
print("Prefixes:")
for d in prefixes:
    print(f"  {d['prefix']} → {d['uri']}")
# Build the map used by get_qname
prefix_map = {_norm_base(d["uri"]): f"{d['prefix']}:" for d in prefixes}

def get_qname(uri):
    s = str(uri)
    norm = _norm_base(s)
    for base in sorted(prefix_map.keys(), key=len, reverse=True):
        if norm == base or norm.startswith(base + '/') or norm.startswith(base + '#'):
            local = s[len(base):]
            if local.startswith(('/', '#', '_')):
                local = local[1:]
            local = local.rstrip()
            if prefix_map[base] == ":":
                return local
            return prefix_map[base] + local
    print(f"Warning: No prefix found for URI: {s}")
    return s

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
        if (super_cls, RDF.type, OWL.Class) in g or str(super_cls).startswith(ns):
            direct_supers.add(super_cls)
    all_supers = set(direct_supers)
    for sup in direct_supers:
        all_supers.update(get_all_class_superclasses(sup, g))
    return all_supers

# Extract classes
classes = set(g.subjects(RDF.type, OWL.Class)) - {OWL.Thing}
print(f"Found {len(classes)} classes in ontology")
if not classes:
    print("No classes found with RDF.type OWL.Class")
else:
    print("Classes found:")
    for cls in classes:
        print("   " + str(cls))

# Filter classes by namespace
local_classes = [cls for cls in classes if str(cls).startswith(ns)]
print(f"Filtered to {len(local_classes)} local classes in namespace {ns}")
if local_classes:
    print("Local classes:")
    for cls in local_classes:
        print("   " + get_qname(cls))

processed_count = 0
errors = []
abstract_map = {}

for cls in local_classes:
    cls_name = get_qname(cls)
    abstract_map[cls_name] = is_abstract(cls, g)

#for name, is_abs in abstract_map.items():
#    print(f"{name}: abstract={is_abs}")

def fmt_title(name: str) -> str:
    return f"<B><I>{name}</I></B>" if abstract_map.get(name, False) else f"<B>{name}</B>"

# Removed ITSPatternThing filtering, as it's not in metr.ofn
for cls in local_classes:
    cls_name = get_qname(cls)
    cls_id = get_id(cls_name)
    print(f"Processing class: {cls_name}")

    try:
        dot = Digraph(
            comment=f"Diagram for {cls_name}",
            format="svg",
            graph_attr={"overlap": "false", "splines": "true", "rankdir": "TB"},
            node_attr={"shape": "none", "fontsize": "12", "fontname": "Arial", "margin": "0"},
            edge_attr={"fontsize": "10", "fontname": "Arial"}
        )

        # Define superclasses
        superclasses = set()
        for super_cls in g.objects(cls, RDFS.subClassOf):
            if (super_cls, RDF.type, OWL.Class) in g or str(super_cls).startswith(ns):
                superclasses.add(get_qname(super_cls))
        for sup in sorted(superclasses):
            sup_id = get_id(sup)
            dot.node(sup_id, label=f'<<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="1"><TR><TD BGCOLOR="lightgray" ALIGN="CENTER">{fmt_title(sup)}</TD></TR></TABLE>>', margin="0")

        # Define main class
        with dot.subgraph() as main_group:
            main_group.attr(rank='max')
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
                        restriction_str = f"«{', '.join(restrictions)}»" if restrictions else ""
                        data_props[prop_name].append((restriction_str, range_name))

            attributes = []
            for prop_name, restrictions in sorted(data_props.items()):
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

        # Define associated classes
        associated_classes = set()
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
            associated_cluster.node('Invis', label='<<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="0" CELLPADDING="1"><TR><TD></TD></TR></TABLE>>', style='invis', margin="0")
            for assoc in sorted(associated_classes):
                assoc_id = get_id(assoc)
                associated_cluster.node(assoc_id, label=f'<<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="1"><TR><TD BGCOLOR="lightgray" ALIGN="CENTER">{fmt_title(assoc)}</TD></TR></TABLE>>', margin="0")

        # Define generalization relationships
        for sup in sorted(superclasses):
            sup_id = get_id(sup)
            dot.edge(cls_id, sup_id, arrowhead="onormal", style="solid")

        # Define invisible association
        dot.edge(cls_id, 'Invis', style="invis")

        # Define object property associations
        combined = defaultdict(dict)
#        print(f"Processing object property restrictions for {cls_name}:")
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
                        reflexive = target_qname == cls_name
#                        print(f"  - Property: {prop_name}, Target: {target_qname}, Cardinality: {label_part}, Reflexive: {reflexive}")

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
                            target_id = get_id(target_qname)
                            reflexive = target_qname == cls_name
                        label_part = "only"
#                        print(f"  - Property: {prop_name}, Target: {target_qname}, Restriction: only, Reflexive: {reflexive}")

                    some_values_from = g.value(restriction, OWL.someValuesFrom)
                    if some_values_from:
                        target = some_values_from
                        target_qname = get_qname(target)
                        target_id = get_id(target_qname)
                        reflexive = target_qname == cls_name
                        label_part = "some"
#                        print(f"  - Property: {prop_name}, Target: {target_qname}, Restriction: some, Reflexive: {reflexive}")

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
            label = f"«{', '.join(sorted(set(label_parts)))}»\\nonProperty: {prop_name}" if label_parts else f"onProperty: {prop_name}"
#            print(f"  - Adding edge: {cls_name} -> {target_qname}, Label: {label}, Style: {style}, Reflexive: {reflexive}")
            if is_union:
                union_id = target_id
                union_label = f'«unionOf»<BR/>[{ " or ".join(union_members) }]'
                dot.node(union_id, f'<<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="1" BGCOLOR="lightyellow"><TR><TD ALIGN="CENTER">{union_label}</TD></TR></TABLE>>', margin="0")
                for member in union_members:
                    assoc_id = get_id(member)
                    dot.edge(union_id, assoc_id, style="dotted", label="member", arrowhead="normal")
            if reflexive:
                dot.edge(cls_id, cls_id, label=label, style=style, arrowhead="normal")
            else:
                dot.edge(cls_id, target_id, label=label, style=style, arrowhead="normal")

        # Save DOT file and render SVG
        print(f"Saving {cls_name}.dot")
        dot_file = os.path.join(diagrams_dir, f"{cls_name}.dot")
        dot.save(dot_file)
        dot.render(dot_file, cleanup=False)
        dot.render(dot_file, format='png', cleanup=False)
        processed_count += 1

    except Exception as e:
        errors.append(f"Error processing {cls_name}: {str(e)}")

print(f"Total processed classes: {processed_count}")
if errors:
    print("Errors occurred:")
    for err in errors:
        print(err)