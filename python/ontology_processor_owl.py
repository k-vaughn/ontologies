import os
import logging
import traceback
from rdflib import Graph, RDF, OWL, URIRef
from utils import get_qname, get_ontology_metadata, _norm_base
from rdflib.namespace import DC, DCTERMS

log = logging.getLogger("owl2mkdocs")

def process_ontology(owl_path: str, errors: list, ontology_info) -> tuple:
    """Process an OWL file and update ontology_info, return graph, namespace, prefix map, classes, local classes, and property map."""
    # Load OWL ontology into RDF graph
    try:
        g = Graph()
        g.parse(owl_path, format='xml')
        log.info("Loaded ontology %s with %d triples", owl_path, len(g))
        # Debug RuleMaker triples
#        rulemaker_uri = URIRef("https://isotc204.org/ontologies/its/regulation#RuleMaker")
#        log.info("Checking triples for RuleMaker (%s):", rulemaker_uri)
#        for s, p, o in g.triples((rulemaker_uri, None, None)):
#            log.info("  Triple: (%s, %s, %s)", s, p, o)
        if len(g) == 0:
            raise ValueError("RDF graph is empty after loading ontology")
    except Exception as e:
        error_msg = f"Failed to load or parse ontology from {owl_path}: {str(e)}\n{traceback.format_exc()}"
        errors.append(error_msg)
        log.error(error_msg)
        return None, None, None, None, None, None

    # Dynamically set default namespace from ontology IRI
    ns = None
    for s in g.subjects(RDF.type, OWL.Ontology):
        ns = str(s)
        break
    if not ns:
        log.warning("No ontology IRI found in OWL file %s; using default namespace", owl_path)
        ns = "https://isotc204.org/ontologies/its/default#"

    # Extract ontology metadata and update ontology_info
    dc_title = get_ontology_metadata(g, ns, DC.title) or "Untitled Ontology"
    dc_description = get_ontology_metadata(g, ns, DC.description) or ""
    ontology_info["title"] = dc_title
    ontology_info["description"] = dc_description
    ontology_info["patterns"] = set()
    ontology_info["non_pattern_classes"] = set()

    # Extract prefixes and create prefix map
    prefix_map = {str(uri): f"{prefix}:" for prefix, uri in g.namespaces()}
    if ns not in prefix_map:
        prefix_map[ns] = ":"
    log.debug("Prefixes for %s:", owl_path)
    for uri, prefix in prefix_map.items():
        log.debug("  %s → %s", prefix, uri)

    # Extract classes
    classes = set(g.subjects(RDF.type, OWL.Class)) - {OWL.Thing}
    # Filter out non-HTTP classes (e.g., unions)
    classes = {cls for cls in classes if str(cls).startswith("http")}
    log.debug("Found %d classes in ontology %s:", len(classes), owl_path)
    for cls in classes:
        log.debug("  %s", str(cls))

    # Filter classes by namespace
    local_classes = [cls for cls in classes if str(cls).startswith(ns)]
    log.debug("Filtered to %d local classes in namespace %s for %s:", len(local_classes), ns, owl_path)
    for cls in local_classes:
        log.debug("  %s", get_qname(g, cls, ns, prefix_map))

    # Create property map: qname to URI
    prop_map = {}
    for p in g.subjects(RDF.type, OWL.ObjectProperty):
        qn = get_qname(g, p, ns, prefix_map)
        prop_map[qn] = p
    for p in g.subjects(RDF.type, OWL.DatatypeProperty):
        qn = get_qname(g, p, ns, prefix_map)
        prop_map[qn] = p

    return g, ns, prefix_map, classes, local_classes, prop_map