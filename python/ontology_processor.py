import os
import logging
import traceback
from rdflib import Graph, RDF, OWL, URIRef
from funowl.converters.functional_converter import to_python
from utils import get_qname, get_ontology_metadata, _norm_base, get_prefix_named_pairs
from rdflib.namespace import DC, DCTERMS

log = logging.getLogger("ofn2mkdocs")

def process_ontology(ofn_path: str, errors: list, ontology_info) -> tuple:
    """Process an OFN file, generate a corresponding TTL file, update ontology_info, and return graph, namespace, prefix map, classes, local classes, and property map."""
    # Check file extension
    if not ofn_path.lower().endswith('.ofn'):
        error_msg = f"Invalid file extension for {ofn_path}. Expected .ofn, skipping."
        errors.append(error_msg)
        log.error(error_msg)
        return None, None, None, None, None, None

    # Load OFN ontology using funowl
    try:
        doc = to_python(ofn_path)
        if not doc:
            raise ValueError("Failed to parse OWL functional syntax document")
        
        # Get default namespace from document
        ns = None
        if hasattr(doc, 'ontology') and doc.ontology and doc.ontology.iri:
            ns = str(doc.ontology.iri)
        if not ns:
            log.warning("No ontology IRI found in OFN file %s; using default namespace", ofn_path)
            ns = "https://isotc204.org/ontologies/its/regulation#"
        log.info("Using namespace for %s: %s", ofn_path, ns)

        # Get prefix map from funowl document
        prefix_pairs = get_prefix_named_pairs(doc, ns)
        prefix_map = {item['uri']: item['prefix'] for item in prefix_pairs}
        log.debug("Prefixes for %s:", ofn_path)
        for item in prefix_pairs:
            log.debug("  %s → %s", item['prefix'], item['uri'])

        # Convert to rdflib Graph
        g = Graph()
        doc.to_rdf(g)
        log.info("Converted to RDF graph with %d triples", len(g))

        # Bind prefixes to graph for serialization and queries
        for item in prefix_pairs:
            prefix = item['prefix'].rstrip(':') if item['prefix'] else ''
            uri = URIRef(item['uri'])
            g.bind(prefix, uri)

        # Debug RuleMaker triples
#        rulemaker_uri = URIRef("https://isotc204.org/ontologies/its/regulation#RuleMaker")
#        log.info("Checking triples for RuleMaker (%s):", rulemaker_uri)
#        for s, p, o in g.triples((rulemaker_uri, None, None)):
#            log.info("  Triple: (%s, %s, %s)", s, p, o)

    except Exception as e:
        error_msg = f"Failed to load or parse ontology from {ofn_path}: {str(e)}\n{traceback.format_exc()}\nEnsure the 'funowl' library is installed (`pip install funowl`) and the .ofn file is valid."
        errors.append(error_msg)
        log.error(error_msg)
        return None, None, None, None, None, None

    # Serialize to TTL
    ttl_path = ofn_path.rsplit('.', 1)[0] + '.ttl'
    try:
        g.serialize(destination=ttl_path, format='turtle')
        log.info("Generated TTL file: %s", ttl_path)
    except Exception as e:
        error_msg = f"Failed to serialize ontology to TTL at {ttl_path}: {str(e)}\n{traceback.format_exc()}"
        errors.append(error_msg)
        log.error(error_msg)
        # Continue processing even if TTL generation fails

    # Extract ontology metadata and update ontology_info
    dc_title = get_ontology_metadata(g, ns, DC.title) or "Untitled Ontology"
    dcterms_description = get_ontology_metadata(g, ns, DCTERMS.description) or ""
    ontology_info["title"] = dc_title
    ontology_info["description"] = dcterms_description
    ontology_info["patterns"] = set()
    ontology_info["non_pattern_classes"] = set()

    # Extract classes
    classes = set(g.subjects(RDF.type, OWL.Class)) - {OWL.Thing}
    classes = {cls for cls in classes if str(cls).startswith("http")}
    log.info("Found %d classes in ontology %s:", len(classes), ofn_path)
    for cls in classes:
        log.info("  %s", str(cls))

    # Filter classes by namespace
    local_classes = [cls for cls in classes if str(cls).startswith(ns)]
    log.info("Filtered to %d local classes in namespace %s for %s:", len(local_classes), ns, ofn_path)
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