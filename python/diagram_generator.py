import os
import logging
from rdflib import Graph, RDF, RDFS, OWL, XSD, URIRef
from graphviz import Digraph
from collections import defaultdict
from utils import get_qname, get_id, fmt_title, get_all_class_superclasses, is_refined_property, get_union_classes

log = logging.getLogger("ofn2mkdocs")

def get_target_info(g: Graph, expr: URIRef, cls_name: str, ns: str, prefix_map: dict) -> tuple:
    """Get target information, handling unions."""
    if not expr:
        return None, False, None, None, False
    union_col = g.value(expr, OWL.unionOf)
    if union_col and union_col != RDF.nil:
        members = get_union_classes(g, expr, ns, prefix_map)  # Already sorted
        if members:
            union_id = f"union_{'_'.join([m.replace(':', '_') for m in members])}"
            return union_id, True, members, None, False
    else:
        target_qname = get_qname(g, expr, ns, prefix_map)
        if target_qname == 'ITSThing':
            return None, False, None, None, False
        target_id = get_id(target_qname.replace(":", "_"))
        reflexive = target_qname == cls_name
        return target_id, False, None, target_qname, reflexive

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
        sup_id = get_id(sup.replace(":", "_"))
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
                    restrictions = []
                    stereotype = "refined" if is_refined_property(g, cls, prop, restriction) else ""
                    # Handle allValuesFrom for data properties
                    all_values_from = g.value(restriction, OWL.allValuesFrom)
                    if all_values_from:
                        union_col = g.value(all_values_from, OWL.unionOf)
                        if union_col and union_col != RDF.nil:
                            union_ranges = get_union_classes(g, all_values_from, ns, prefix_map)
                            range_name = f"({' or '.join(union_ranges)})"
                        else:
                            range_name = get_qname(g, all_values_from, ns, prefix_map).split(":")[-1]
                        restrictions.append("only")
                    # Handle qualified cardinalities for data properties
                    on_data_range = g.value(restriction, OWL.onDataRange)
                    qualified_card = g.value(restriction, OWL.qualifiedCardinality)
                    min_qualified_card = g.value(restriction, OWL.minQualifiedCardinality)
                    max_qualified_card = g.value(restriction, OWL.maxQualifiedCardinality)
                    if qualified_card:
                        restrictions.append(f"exactly {qualified_card}")
                    if min_qualified_card:
                        restrictions.append(f"min {min_qualified_card}")
                    if max_qualified_card:
                        restrictions.append(f"max {max_qualified_card}")
                    if on_data_range:
                        union_col = g.value(on_data_range, OWL.unionOf)
                        if union_col and union_col != RDF.nil:
                            union_ranges = get_union_classes(g, on_data_range, ns, prefix_map)
                            range_name = f"({' or '.join(union_ranges)})"
                        else:
                            range_name = get_qname(g, on_data_range, ns, prefix_map).split(":")[-1]
                    # Non-qualified cardinalities
                    card = g.value(restriction, OWL.cardinality)
                    min_card = g.value(restriction, OWL.minCardinality)
                    max_card = g.value(restriction, OWL.maxCardinality)
                    if card:
                        restrictions.append(f"exactly {card}")
                    if min_card:
                        restrictions.append(f"min {min_card}")
                    if max_card:
                        restrictions.append(f"max {max_card}")
                    restriction_str = f"«{', '.join(restrictions)}»" if restrictions else ""
                    data_props[prop_name].append((restriction_str, range_name, stereotype))

        attributes = []
        for prop_name, restrictions in sorted(data_props.items()):
            all_restrictions = []
            range_names = set()
            stereotypes = set()
            for restriction_str, range_name, stereotype in restrictions:
                if restriction_str:
                    all_restrictions.append(restriction_str)
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
                    target_id, is_union, union_members, target_qname, _ = get_target_info(g, on_class, cls_name, ns, prefix_map)
                    if target_id:
                        if is_union:
                            associated_classes.update(union_members)
                        else:
                            if target_qname != cls_name and target_qname not in superclasses and target_qname != 'ITSThing':
                                associated_classes.add(target_qname)
                all_values_from = g.value(restriction, OWL.allValuesFrom)
                if all_values_from and all_values_from != OWL.Thing:
                    target_id, is_union, union_members, target_qname, _ = get_target_info(g, all_values_from, cls_name, ns, prefix_map)
                    if target_id:
                        if is_union:
                            associated_classes.update(union_members)
                        else:
                            if target_qname != cls_name and target_qname not in superclasses and target_qname != 'ITSThing':
                                associated_classes.add(target_qname)
                some_values_from = g.value(restriction, OWL.someValuesFrom)
                if some_values_from and some_values_from != OWL.Thing:
                    target_id, is_union, union_members, target_qname, _ = get_target_info(g, some_values_from, cls_name, ns, prefix_map)
                    if target_id:
                        if is_union:
                            associated_classes.update(union_members)
                        else:
                            if target_qname != cls_name and target_qname not in superclasses and target_qname != 'ITSThing':
                                associated_classes.add(target_qname)

    with dot.subgraph(name='cluster_associated') as associated_cluster:
        associated_cluster.attr(style='invis', label='')
        associated_cluster.node('Invis', label='<<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="0" CELLPADDING="1"><TR><TD></TD></TR></TABLE>>', style='invis', margin="0")
        for assoc in sorted(associated_classes):
            assoc_id = get_id(assoc.replace(":", "_"))
            attrs = dict(
                label=f'<<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="1"><TR><TD BGCOLOR="lightgray" ALIGN="CENTER">{fmt_title(assoc, global_all_classes, ns, abstract_map)}</TD></TR></TABLE>>',
                URL=f"../classes/{assoc}.md" if assoc in global_all_classes else None,
                margin="0"
            )
            associated_cluster.node(assoc_id, **attrs)            

    # Define generalization relationships
    for sup in sorted(superclasses):
        sup_id = get_id(sup.replace(":", "_"))
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
                label_parts = []
                is_union = False
                union_members = None
                reflexive = False
                target_qname = None

                # Handle qualified cardinalities for object properties
                on_class = g.value(restriction, OWL.onClass)
                qualified_card = g.value(restriction, OWL.qualifiedCardinality)
                min_qualified_card = g.value(restriction, OWL.minQualifiedCardinality)
                max_qualified_card = g.value(restriction, OWL.maxQualifiedCardinality)
                if qualified_card:
                    label_parts.append(f"exactly {qualified_card}")
                if min_qualified_card:
                    label_parts.append(f"min {min_qualified_card}")
                if max_qualified_card:
                    label_parts.append(f"max {max_qualified_card}")
                if label_parts and on_class:
                    target_id, is_union, union_members, target_qname, reflexive = get_target_info(g, on_class, cls_name, ns, prefix_map)
                    if target_id:
                        log.debug("  - Property: %s, Target: %s, Cardinality: %s, Reflexive: %s, Refined: %s", prop_name, target_qname or "union", ' '.join(label_parts), reflexive, is_refined)

                # Handle allValuesFrom
                all_values_from = g.value(restriction, OWL.allValuesFrom)
                if all_values_from:
                    target_id, is_union, union_members, target_qname, reflexive = get_target_info(g, all_values_from, cls_name, ns, prefix_map)
                    if target_id:
                        label_parts.append("only")
                        log.debug("  - Property: %s, Target: %s, Restriction: only, Reflexive: %s, Refined: %s", prop_name, target_qname or "union", reflexive, is_refined)

                # Handle someValuesFrom
                some_values_from = g.value(restriction, OWL.someValuesFrom)
                if some_values_from:
                    target_id, is_union, union_members, target_qname, reflexive = get_target_info(g, some_values_from, cls_name, ns, prefix_map)
                    if target_id:
                        label_parts.append("some")
                        log.debug("  - Property: %s, Target: %s, Restriction: some, Reflexive: %s, Refined: %s", prop_name, target_qname or "union", reflexive, is_refined)

                # Non-qualified cardinalities
                card = g.value(restriction, OWL.cardinality)
                min_card = g.value(restriction, OWL.minCardinality)
                max_card = g.value(restriction, OWL.maxCardinality)
                if card:
                    label_parts.append(f"exactly {card}")
                if min_card:
                    label_parts.append(f"min {min_card}")
                if max_card:
                    label_parts.append(f"max {max_card}")

                if target_id and label_parts:
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
                    combined[key]['label_parts'].extend(label_parts)
                    combined[key]['style'] = "dashed" if is_refined else combined[key]['style']  # Update style if refined

    prev = 'Invis'
    for assoc in sorted(associated_classes):
        assoc_id = get_id(assoc.replace(":", "_"))
        dot.edge(prev, assoc_id, style="invis")
        prev = assoc_id

    created_unions = set()
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
            label = prop_name + " " + label_prefix
        else:
            label = "onProperty: " + prop_name + " " + label_prefix
        log.debug("  - Adding edge: %s -> %s, Label: %s, Style: %s, Reflexive: %s", cls_name, target_qname or "union", label, style, reflexive)
        if is_union:
            union_id = target_id
            if union_id not in created_unions:
                union_label = f"«unionOf»<BR/>[{ ' or '.join(union_members) }]"
                dot.node(union_id, f'<<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="1" BGCOLOR="lightyellow"><TR><TD ALIGN="CENTER">{union_label}</TD></TR></TABLE>>', margin="0")
                for member in union_members:
                    assoc_id = get_id(member.replace(":", "_"))
                    associated_classes.add(member)
                    dot.node(
                        assoc_id,
                        label=f'<<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="1"><TR><TD BGCOLOR="lightgray" ALIGN="CENTER">{fmt_title(member, global_all_classes, ns, abstract_map)}</TD></TR></TABLE>>',
                        URL=f"../classes/{member}.md" if member in global_all_classes else None,
                        margin="0"
                    )
                    dot.edge(union_id, assoc_id, style="dotted", label="member", arrowhead="normal")
                created_unions.add(union_id)
        if reflexive:
            dot.edge(cls_id, cls_id, label=label, style=style, arrowhead="normal")
        else:
            dot.edge(cls_id, target_id, label=label, style=style, arrowhead="normal")

    # Save DOT file and render SVG/PNG
    log.debug("Saving diagram for %s", cls_name)
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