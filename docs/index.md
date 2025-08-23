# Annotations

## Namespaces

This page refers to the following namespaces:

- dcterms: http://purl.org/dc/terms/
- owl: http://www.w3.org/2002/07/owl#
- protoge: http://protege.stanford.edu/ontologies/metadata#
- skos: http://www.w3.org/2004/02/skos/core#

This minimizes imports to just four namespaces (RDFS and OWL are typically already imported in any OWL ontology, so effectively adding only DCTERMS and SKOS).

## Recommended Annotations

The ITS Ontology Registry recommends using the following annotations when describing ontological elements. 

| Annotation           | Rationale and Usage Notes                                                  |
|----------------------|----------------------------------------------------------------------------|
| `skos:definition`    | Normative definition of the entity. Use no more than once per language.    |
| `dcterms:source`     | Cite origin or reference; can use multiples.                               |
| `skos:note`          | Informative details as needed; can use multiples.                          |
| `skos:example`       | Illustrative example; can use multiples.                                   |
| `dcterms:license`    | Identifies legal usage terms; used in ontology header.                     |
| `dcterms:created`    | For creation date; used for ontology IRI.                                  |
| `dcterms:modified`   | Identifies the date of an update date; pair with repeatable `skos:changeNote` for descriptions. |
| `skos:changeNote`    | Provides a description of the update date; pair with `dcterms:modified`    |
| `dcterms:replaces`   | Identifies a historic ontological element that is replaced by a new ontological element; repeatable if needed. |
| `dcterms:isReplacedBy`| Identifies a new element that replaces a historic element; repeatable if needed. Inverse of the above |
| `owl:deprecated`     | Boolean (true for deprecated/obsolete)                                     |
| `:pii`               | Boolean (true for personally identifiable information)                     |
| `protoge:abstract`  | Indicates that the class is not intended for direct instantiation.         |
