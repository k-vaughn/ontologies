import os
import logging
import tempfile
from rdflib import Graph, RDF, OWL
from funowl.converters.functional_converter import to_python
from utils import get_qname, get_ontology_metadata, get_prefix_named_pairs, _norm_base
from rdflib.namespace import DC, DCTERMS

log = logging.getLogger("ofn2mkdocs")

def preprocess_ofn_file(ofn_path: str) -> str:
    """Preprocess .ofn file to remove SWRL rules and related constructs."""
    log.debug("Preprocessing %s to remove SWRL rules and related constructs", ofn_path)
    with open(ofn_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # Filter out SWRL-related lines
    swrl_keywords = ['DLSafeRule', 'Variable', 'Atom', 'Body', 'Head', 'swrl:', 'SWRL', 'Rule', 'swrlb:']
    filtered_lines = []
    line_count = 0
    max_lines = 100000  # Safeguard to prevent infinite loop

    for line in lines:
        line_count += 1
        if line_count > max_lines:
            log.error("Preprocessing aborted: Maximum line limit (%d) reached for %s", max_lines, ofn_path)
            raise ValueError(f"Preprocessing aborted: Maximum line limit ({max_lines}) reached")
        if any(kw in line for kw in swrl_keywords):
            log.debug("Skipping SWRL-related line: %s", line.strip())
            continue
        filtered_lines.append(line)

    log.debug("Processed %d lines, kept %d lines after SWRL filtering", line_count, len(filtered_lines))

    # Write filtered content to a temporary file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.ofn', delete=False, encoding='utf-8') as temp_f:
        temp_f.writelines(filtered_lines)
        temp_path = temp_f.name

    log.debug("Created temporary filtered .ofn file: %s", temp_path)
    return temp_path

def process_ontology(ofn_path: str, errors: list, ontology_info) -> tuple:
    """Process an OFN file and update ontology_info, return graph, namespace, prefix map, classes, local classes, and property map."""
    # Preprocess .ofn file to remove SWRL rules
    temp_ofn_path = preprocess_ofn_file(ofn_path)

    # Load OFN ontology and convert to RDF graph
    try:
        ont_doc = to_python(temp_ofn_path)
        g = Graph()
        ont_doc.to_rdf(g)
        log.info("Loaded ontology %s with %d triples", ofn_path, len(g))
        if len(g) == 0:
            raise ValueError("RDF graph is empty after loading ontology")
    except Exception as e:
        error_msg = f"Failed to load or parse ontology from {ofn_path} (temp file: {temp_ofn_path}): {str(e)}\n{traceback.format_exc()}"
        errors.append(error_msg)
        log.error(error_msg)
        os.remove(temp_ofn_path)
        return None, None, None, None, None, None
    finally:
        if os.path.exists(temp_ofn_path):
            os.remove(temp_ofn_path)

    # Dynamically set default namespace from ontology IRI
    try:
        ns = str(ont_doc.ontology.iri) if ont_doc.ontology and ont_doc.ontology.iri else None
    except AttributeError:
        ns = None
    if not ns:
        log.warning("No ontology IRI found in OFN file %s; using example.com namespace", ofn_path)
        ns = "https://example.com/ontology#"
    log.info("Using default namespace for %s: %s", ofn_path, ns)

    # Extract ontology metadata and update ontology_info
    dc_title = get_ontology_metadata(g, ns, DC.title) or "Untitled Ontology"
    dcterms_description = get_ontology_metadata(g, ns, DCTERMS.description) or ""
    if not dc_title:
        log.warning("No dc:title found for ontology in %s", ofn_path)
    if not dcterms_description:
        log.warning("No dcterms:description found for ontology in %s", ofn_path)
    ontology_info["title"] = dc_title
    ontology_info["description"] = dcterms_description
    ontology_info["patterns"] = set()
    ontology_info["non_pattern_classes"] = set()

    # Extract prefixes and create prefix map
    prefixes = get_prefix_named_pairs(ont_doc, ns)
    prefix_map = {d["uri"]: f"{d['prefix']}:" for d in prefixes}
    log.info("Prefixes for %s:", ofn_path)
    for d in prefixes:
        log.info("  %s → %s", d['prefix'], d['uri'])

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
        log.info("Local classes in %s: %s", ofn_path, [get_qname(g, cls, ns, prefix_map) for cls in local_classes])

    # Create property map: qname to URI
    prop_map = {}
    for p in g.subjects(RDF.type, OWL.ObjectProperty):
        qn = get_qname(g, p, ns, prefix_map)
        prop_map[qn] = p
    for p in g.subjects(RDF.type, OWL.DatatypeProperty):
        qn = get_qname(g, p, ns, prefix_map)
        prop_map[qn] = p

    return g, ns, prefix_map, classes, local_classes, prop_map