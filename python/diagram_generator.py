import os
import logging
from rdflib import Graph, RDF, RDFS, OWL, XSD, URIRef
from graphviz import Digraph
from collections import defaultdict
from utils import get_qname, get_id, fmt_title, get_all_class_superclasses, is_refined_property

log = logging.getLogger("ofn2mkdocs")

def generate_diagram(g: Graph, cls: URIRef, cls_name: str, cls_id: str, ns: str, global_all_classes: set, abstract_map: dict, ofn_path: str, errors: list, prefix_map: dict):
    """Generate and render a class diagram using Graphviz."""
    diagrams_dir = os.path.join(os.path.dirname(ofn_path), "diagrams")

    # Initialize Digraph
    dot = Digraph(
        comment=f"Diagram for {cls_name}",
        format="svg",
        graph_attr={"overlap": "false", "splines": "true", "rankdir": "TB"},
        node_attr={"shape": "none", "fontsize": "12", "fontname": "Arial", "margin": "0"},
        edge_attr={"fontsize": "11", "fontname": "Arial"}
    )

    # Define superclasses (only direct superclasses via rdfs:subClassOf)
    superclasses = set()
    for super_cls in g.objects(cls, RDFS.subClassOf):
        if isinstance(super_cls, URIRef) and super_cls != OWL.Thing and (super_cls, RDF.type, OWL.Class) in g:
            superclasses.add(get_qname(g, super_cls, ns, prefix_map))        	
    for sup in sorted(superclasses):
        sup_id = get_id(sup)
        dot.node(
            sup_id,
            label=f'<<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="1"><TR><TD BGCOLOR="lightgray" ALIGN="CENTER">{fmt_title(sup, global_all_classes, ns, abstract_map)}</TD></TR></TABLE>>',
            URL=f"../classes/{sup}.md" if sup in global_all_classes else None,
            margin="0"
        )

    # Define main class with direct or refined datatype properties
    with dot.subgraph() as main_group:
        main_group.attr(rank='max')
        data_props = defaultdict(list)
        # Collect direct or refined datatype properties
        for restriction in g.objects(cls, RDFS.subClassOf):
            if (restriction, RDF.type, OWL.Restriction) in g:
                prop = g.value(restriction, OWL.onProperty)
                if prop and (prop, RDF.type, OWL.DatatypeProperty) in g:
                    prop_name = get_qname(g, prop, ns, prefix_map)
                    range_type = g.value(prop, RDFS.range) or XSD.string
                    range_name = get_qname(g, range_type, ns, prefix_map).split(":")[-1]
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
        main_label = f'<<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="1"><TR><TD BGCOLOR="lightgray" ALIGN="CENTER" PORT="e">{fmt_title(cls_name, global_all_classes, ns, abstract_map)}</TD></TR>{attributes_html}</TABLE>>'
        main_group.node(
            cls_id,
            main_label,
            URL=f"../classes/{cls_name}.md" if cls_name in global_all_classes else None,
            margin="0"
        )

    # Define associated classes (via object properties)
    associated_classes = set()
    for restriction in g.objects(cls, RDFS.subClassOf):
        if (restriction, RDF.type, OWL.Restriction) in g:
            prop = g.value(restriction, OWL.onProperty)
            if prop and (prop, RDF.type, OWL.ObjectProperty) in g:
                on_class = g.value(restriction, OWL.onClass)
                if on_class and on_class != OWL.Thing:
                    on_class_qn = get_qname(g, on_class, ns, prefix_map)
                    if on_class_qn != cls_name and on_class_qn not in superclasses and on_class_qn != 'ITSThing':
                        associated_classes.add(on_class_qn)
                all_values_from = g.value(restriction, OWL.allValuesFrom)
                if all_values_from and all_values_from != OWL.Thing:
                    union_collection = g.value(all_values_from, OWL.unionOf)
                    if union_collection and union_collection != RDF.nil:
                        current = union_collection
                        while current != RDF.nil:
                            first = g.value(current, RDF.first)
                            if first and first != OWL.Thing:
                                member_qn = get_qname(g, first, ns, prefix_map)
                                if member_qn != cls_name and member_qn not in superclasses and member_qn != 'ITSThing':
                                    associated_classes.add(member_qn)
                            current = g.value(current, RDF.rest)
                    else:
                        all_qn = get_qname(g, all_values_from, ns, prefix_map)
                        if all_qn != cls_name and all_qn not in superclasses and all_qn != 'ITSThing':
                            associated_classes.add(all_qn)
                some_values_from = g.value(restriction, OWL.someValuesFrom)
                if some_values_from and some_values_from != OWL.Thing:
                    some_qn = get_qname(g, some_values_from, ns, prefix_map)
                    if some_qn != cls_name and some_qn not in superclasses and some_qn != 'ITSThing':
                        associated_classes.add(some_qn)

    with dot.subgraph(name='cluster_associated') as associated_cluster:
        associated_cluster.attr(style='invis', label='')
        associated_cluster.node('Invis', label='<<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="0" CELLPADDING="1"><TR><TD></TD></TR></TABLE>>', style='invis', margin="0")
        for assoc in sorted(associated_classes):
            assoc_id = get_id(assoc)
            attrs = dict(
                label=f'<<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="1">'
                    f'<TR><TD BGCOLOR="lightgray" ALIGN="CENTER">{fmt_title(assoc, global_all_classes, ns, abstract_map)}</TD></TR>'
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
                prop_name = get_qname(g, prop, ns, prefix_map)
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
                    target_qname = get_qname(g, on_class, ns, prefix_map)
                    if target_qname == 'ITSThing':
                        continue  # Skip problematic classes
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
                                member_qn = get_qname(g, first, ns, prefix_map)
                                if member_qn != 'ITSThing':
                                    members.append(member_qn)
                            current = g.value(current, RDF.rest)
                        if members:
                            members.sort()
                            union_id = f"Union_{get_id(prop_name)}"
                            target_id = union_id
                            is_union = True
                            union_members = members
                    else:
                        target_qname = get_qname(g, target, ns, prefix_map)
                        if target_qname == 'ITSThing':
                            continue  # Skip problematic classes
                        target_id = get_id(target_qname)
                        reflexive = target_qname == cls_name
                    label_part = "only"
                    log.debug("  - Property: %s, Target: %s, Restriction: only, Reflexive: %s, Refined: %s", prop_name, target_qname, reflexive, is_refined)

                some_values_from = g.value(restriction, OWL.someValuesFrom)
                if some_values_from:
                    target = some_values_from
                    target_qname = get_qname(g, some_values_from, ns, prefix_map)
                    if target_qname == 'ITSThing':
                        continue  # Skip problematic classes
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
#            union_label = f'«unionOf»<BR/>({" or ".join([fmt_title(m, global_all_classes, ns, abstract_map) for m in union_members])})'
            dot.node(union_id, f'<<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="1" BGCOLOR="lightyellow"><TR><TD ALIGN="CENTER">{union_label}</TD></TR></TABLE>>', margin="0")
            for member in union_members:
                assoc_id = get_id(member)
                associated_classes.add(member)
                dot.node(
                    assoc_id,
                    label=f'<<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="1"><TR><TD BGCOLOR="lightgray" ALIGN="CENTER">{fmt_title(member, global_all_classes, ns, abstract_map)}</TD></TR></TABLE>>',
                    URL=f"../classes/{member}.md" if member in global_all_classes else None,
                    margin="0"
                )
                dot.edge(union_id, assoc_id, style="dotted", label="member", arrowhead="normal")
        if reflexive:
            dot.edge(cls_id, cls_id, label=label, style=style, arrowhead="normal")
        else:
            dot.edge(cls_id, target_id, label=label, style=style, arrowhead="normal")

    # Save DOT file and render SVG/PNG
    log.info("Saving diagram for %s", cls_name)
    try:
        dot_file = os.path.join(diagrams_dir, f"{cls_name}")
        dot.save(dot_file)
        dot.render(dot_file, cleanup=False)
        dot.render(dot_file, format='png', cleanup=False)
    except Exception as e:
        error_msg = f"Error rendering diagram for {cls_name} from {ofn_path}: {str(e)}\n{traceback.format_exc()}"
        errors.append(error_msg)
        log.error(error_msg)
        raise