import re
import logging
from typing import Optional, Iterable, Tuple, List
from rdflib import Graph, RDF, RDFS, OWL, URIRef, Literal
from rdflib.namespace import DC, DCTERMS, SKOS

log = logging.getLogger("ofn2mkdocs")

# -------------------- namespaces --------------------
DESC_PROPS = (DC.description, SKOS.definition, RDFS.comment, DCTERMS.description)
SKIP_IN_OTHER = set(DESC_PROPS) | {RDFS.label, DCTERMS.description, SKOS.note, SKOS.example}

def _norm_base(u: str) -> str:
    return u.rstrip('/#')

def get_prefix_named_pairs(ontology_doc, ns: str):
    """Return [{'prefix': <str>, 'uri': <str>}, ...] from funowl PrefixDeclarations,
    handling different return shapes of as_prefixes() across funowl versions."""
    pd = getattr(ontology_doc, "prefixDeclarations", None)
    if not pd:
        log.debug("No prefix declarations found, using default namespace: %s", ns)
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

    log.debug("Prefixes extracted: %s", out)
    return out

def get_qname(g: Graph, uri: URIRef, ns: str, prefix_map: dict):
    if uri is None or not str(uri).strip():
        log.error("Invalid URI provided to get_qname: %s", uri)
        return "INVALID_URI"
    s = str(uri)
    norm = _norm_base(s)
    log.debug("Processing URI: %s, normalized: %s, namespace: %s", s, norm, ns)
    if norm == _norm_base(ns) or s.startswith(ns):
        local = s[len(_norm_base(ns)):]
        if local.startswith(('/', '#', '_')):
            local = local[1:]
        qname = local.rstrip()
        log.debug("Matched default namespace, returning QName: %s", qname)
        return qname
    if not prefix_map:
        log.warning("Empty prefix map for URI: %s, namespace: %s", s, ns)
        return s
    for base in sorted(prefix_map.keys(), key=len, reverse=True):
        base_norm = _norm_base(base)
        log.debug("Checking prefix base: %s, normalized: %s", base, base_norm)
        if s == base or s.startswith(base) or norm == base_norm or norm.startswith(base_norm):
            local = s[len(base):]
            if local.startswith(('/', '#', '_')):
                local = local[1:]
            local = local.rstrip()
            if not local:
                log.debug("No local part after prefix %s, using base URI", base)
                local = s
            if prefix_map[base] == ":":
                qname = local
            else:
                qname = prefix_map[base] + local
            log.debug("Matched prefix %s, returning QName: %s", base, qname)
            return qname
    log.warning("No prefix found for URI: %s, namespace: %s, prefix_map: %s", s, ns, prefix_map)
    return s

def get_label(g: Graph, c: URIRef) -> str:
    if c is None:
        log.error("Invalid class URI provided to get_label: None")
        return "INVALID_CLASS"
    for _, _, lbl in g.triples((c, RDFS.label, None)):
        if isinstance(lbl, Literal):
            return str(lbl)
    return str(c).split('#')[-1]

def get_first_literal(g: Graph, subj: URIRef, preds: Iterable[URIRef]) -> Optional[str]:
    if subj is None:
        log.error("Invalid subject URI provided to get_first_literal: None")
        return None
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

def iter_annotations(g: Graph, subj: URIRef, ns: str, prefix_map: dict) -> Iterable[Tuple[str, str]]:
    """Yield (predicate, value) pairs for annotations as readable strings,
    excluding those in SKIP_IN_OTHER."""
    if subj is None:
        log.error("Invalid subject URI provided to iter_annotations: None")
        return
    for p, o in g.predicate_objects(subj):
        if not isinstance(p, URIRef):
            continue
        if p in SKIP_IN_OTHER:
            continue
        if isinstance(o, Literal):
            yield (get_qname(g, p, ns, prefix_map), str(o))

def get_union_classes(g: Graph, union: URIRef, ns: str, prefix_map: dict) -> List[str]:
    classes = []
    current = g.value(union, OWL.unionOf)
    while current and current != RDF.nil:
        first = g.value(current, RDF.first)
        if first:
            classes.append(get_qname(g, first, ns, prefix_map))
        current = g.value(current, RDF.rest)
    return sorted(classes)

def class_restrictions(g: Graph, c: URIRef, ns: str, prefix_map: dict) -> List[Tuple[str, str]]:
    """Extract OWL restrictions and subClassOf constraints for Markdown output."""
    if c is None:
        log.error("Invalid class URI provided to class_restrictions: None")
        return []
    rows = []
    # Handle basic rdfs:subClassOf
    for _, _, super_cls in g.triples((c, RDFS.subClassOf, None)):
        if isinstance(super_cls, URIRef) and super_cls != OWL.Thing and (super_cls, RDF.type, OWL.Class) in g:
            rows.append(("rdfs:subClassOf", get_qname(g, super_cls, ns, prefix_map)))

    # Handle OWL restrictions
    for _, _, restr in g.triples((c, RDFS.subClassOf, None)):
        if (restr, RDF.type, OWL.Restriction) in g:
            on_prop = None
            for _, _, onp in g.triples((restr, OWL.onProperty, None)):
                if isinstance(onp, URIRef):
                    on_prop = get_qname(g, onp, ns, prefix_map)
            # AllValuesFrom
            all_values_from = g.value(restr, OWL.allValuesFrom)
            if all_values_from:
                if g.value(all_values_from, OWL.unionOf):
                    union_classes = get_union_classes(g, all_values_from, ns, prefix_map)
                    rows.append((on_prop, f"only ({' or '.join(union_classes)})"))
                else:
                    rows.append((on_prop, f"only {get_qname(g, all_values_from, ns, prefix_map)}"))
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
                    target_qname = get_qname(g, on_class, ns, prefix_map)
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
                    range_name = get_qname(g, on_data_range, ns, prefix_map)
                    rows.append((on_prop, f"{' '.join(label_parts)} {range_name}"))
            # Non-qualified cardinalities
            card = g.value(restr, OWL.cardinality)
            min_card = g.value(restr, OWL.minCardinality)
            max_card = g.value(restr, OWL.maxCardinality)
            if card:
                range_type = g.value(on_prop, RDFS.range) if on_prop else None
                if range_type:
                    range_name = get_qname(g, range_type, ns, prefix_map)
                    rows.append((on_prop, f"exactly {card} {range_name}"))
                else:
                    rows.append((on_prop, f"exactly {card}"))
            if min_card:
                range_type = g.value(on_prop, RDFS.range) if on_prop else None
                if range_type:
                    range_name = get_qname(g, range_type, ns, prefix_map)
                    rows.append((on_prop, f"min {min_card} {range_name}"))
                else:
                    rows.append((on_prop, f"min {min_card}"))
            if max_card:
                range_type = g.value(on_prop, RDFS.range) if on_prop else None
                if range_type:
                    range_name = get_qname(g, range_type, ns, prefix_map)
                    rows.append((on_prop, f"max {max_card} {range_name}"))
                else:
                    rows.append((on_prop, f"max {max_card}"))
    return rows

def hyperlink_class(name: str, all_classes: set, ns: str):
    """Create a hyperlink only for classes in the local namespace."""
    if ':' not in name and name in all_classes:  # Local class without prefix
        return f"[{name}]({name}.md)"
    return name

def fmt_title(name: str, all_classes: set, ns: str, abstract_map: dict) -> str:
    """Format class title for Graphviz, with URL attribute for local classes."""
    is_local = ':' not in name and name in all_classes
    display_name = f"<I>{name}</I>" if abstract_map.get(name, False) else name
    return display_name

def is_abstract(cls, g, ns):
    if cls is None:
        log.error("Invalid class URI provided to is_abstract: None")
        return False
    abstract = g.value(cls, URIRef(ns + "abstract"))
    if abstract is None:
        abstract = g.value(cls, URIRef("http://protege.stanford.edu/ontologies/metadata#abstract"))
    return abstract is not None and str(abstract).lower() == "true"

def get_id(qname):
    if not qname:
        log.error("Invalid qname provided to get_id: %s", qname)
        return "INVALID_QNAME"
    if ':' in qname:
        prefix, local = qname.split(':', 1)
        return prefix + '_' + local
    return qname

def get_all_class_superclasses(cls, g):
    if cls is None:
        log.error("Invalid class URI provided to get_all_class_superclasses: None")
        return set()
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
    if cls is None or prop is None or restriction is None:
        log.error("Invalid input to is_refined_property: cls=%s, prop=%s, restriction=%s", cls, prop, restriction)
        return False
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
    if not name:
        log.error("Invalid name provided to insert_spaces: %s", name)
        return "INVALID_NAME"
    name = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1 \2', name)
    name = re.sub(r'([a-z\d])([A-Z])', r'\1 \2', name)
    return name