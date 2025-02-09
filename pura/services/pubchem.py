# -*- coding: utf-8 -*-
"""
PubChemPy

Python interface for the PubChem PUG REST service.
https://github.com/mcs07/PubChemPy
"""
from queue import Queue
from urllib.parse import quote, urlencode
from pura.services import Service
from pura.compound import CompoundIdentifier, CompoundIdentifierType
from pura.utils import inverse_map
from aiohttp import ClientSession
from aiohttp.web_exceptions import (
    HTTPBadRequest,
    HTTPNotFound,
    HTTPServiceUnavailable,
    HTTPMethodNotAllowed,
    HTTPGatewayTimeout,
    HTTPNotImplemented,
    HTTPInternalServerError,
)
from typing import List, Optional, Union
import logging


logger = logging.getLogger(__name__)


text_types = str, bytes


API_BASE = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
AUTOCOMPLETE_BASE = "https://pubchem.ncbi.nlm.nih.gov/rest/autocomplete"

INPUT_IDENTIFIER_MAP = {
    CompoundIdentifierType.SMILES: "smiles",  # changed to smiles from CanonicalSMILES
    # CompoundIdentifierType.MOLBLOCK: "mol",
    CompoundIdentifierType.INCHI: "InChI",
    CompoundIdentifierType.IUPAC_NAME: "IUPACName",
    CompoundIdentifierType.INCHI_KEY: "InChIKey",
    # CompoundIdentifierType.XYZ: "xyz",
    CompoundIdentifierType.NAME: "name",
    CompoundIdentifierType.PUBCHEM_CID: "cid",
}

OUTPUT_IDENTIFIER_MAP = {
    CompoundIdentifierType.SMILES: "CanonicalSMILES",
    CompoundIdentifierType.INCHI: "InChI",
    CompoundIdentifierType.IUPAC_NAME: "IUPACName",
    CompoundIdentifierType.INCHI_KEY: "InChIKey",
    CompoundIdentifierType.TITLE: "Title",  # Added title
    CompoundIdentifierType.PUBCHEM_CID: "CID",
    CompoundIdentifierType.ISOMERIC_SMILES: "IsomericSMILES",
}
PROPERTY_EXCEPTIONS = [OUTPUT_IDENTIFIER_MAP.get(CompoundIdentifierType.PUBCHEM_CID)]
# Allows properties to optionally be specified as underscore_separated, consistent with Compound attributes
PROPERTY_MAP = {
    "molecular_formula": "MolecularFormula",
    "molecular_weight": "MolecularWeight",
    "canonical_smiles": "CanonicalSMILES",
    "isomeric_smiles": "IsomericSMILES",
    "inchi": "InChI",
    "inchikey": "InChIKey",
    "iupac_name": "IUPACName",
    "xlogp": "XLogP",
    "exact_mass": "ExactMass",
    "monoisotopic_mass": "MonoisotopicMass",
    "tpsa": "TPSA",
    "complexity": "Complexity",
    "charge": "Charge",
    "h_bond_donor_count": "HBondDonorCount",
    "h_bond_acceptor_count": "HBondAcceptorCount",
    "rotatable_bond_count": "RotatableBondCount",
    "heavy_atom_count": "HeavyAtomCount",
    "isotope_atom_count": "IsotopeAtomCount",
    "atom_stereo_count": "AtomStereoCount",
    "defined_atom_stereo_count": "DefinedAtomStereoCount",
    "undefined_atom_stereo_count": "UndefinedAtomStereoCount",
    "bond_stereo_count": "BondStereoCount",
    "defined_bond_stereo_count": "DefinedBondStereoCount",
    "undefined_bond_stereo_count": "UndefinedBondStereoCount",
    "covalent_unit_count": "CovalentUnitCount",
    "volume_3d": "Volume3D",
    "conformer_rmsd_3d": "ConformerModelRMSD3D",
    "conformer_model_rmsd_3d": "ConformerModelRMSD3D",
    "x_steric_quadrupole_3d": "XStericQuadrupole3D",
    "y_steric_quadrupole_3d": "YStericQuadrupole3D",
    "z_steric_quadrupole_3d": "ZStericQuadrupole3D",
    "feature_count_3d": "FeatureCount3D",
    "feature_acceptor_count_3d": "FeatureAcceptorCount3D",
    "feature_donor_count_3d": "FeatureDonorCount3D",
    "feature_anion_count_3d": "FeatureAnionCount3D",
    "feature_cation_count_3d": "FeatureCationCount3D",
    "feature_ring_count_3d": "FeatureRingCount3D",
    "feature_hydrophobe_count_3d": "FeatureHydrophobeCount3D",
    "effective_rotor_count_3d": "EffectiveRotorCount3D",
    "conformer_count_3d": "ConformerCount3D",
}


class PubChem(Service):
    """PubhChem is a service offered by the NIH in the United States.

    Paramaters
    ----------
    autocomplete: bool
        If the input identifier cannot be found, you can try autocomplete to have PubChem find alternative names.
        Default is False.

    Notes
    -----
    Pubchem can throttle with lots of requests: https://pubchemdocs.ncbi.nlm.nih.gov/dynamic-request-throttling

    """

    def __init__(self, autocomplete: bool = False, autocomplete_limit: int = 1) -> None:
        self.autocomplete = autocomplete
        self.autocomplete_limit = autocomplete_limit
        super().__init__()

    async def resolve_compound(
        self,
        session: ClientSession,
        input_identifier: CompoundIdentifier,
        output_identifier_types: List[CompoundIdentifierType],
    ) -> List[Union[CompoundIdentifierType, None]]:
        namespace = INPUT_IDENTIFIER_MAP.get(input_identifier.identifier_type)
        # print(namespace)
        if namespace is None:
            raise ValueError(
                f"{input_identifier.identifier_type} is not one of the valid identifier types for PubChem."
            )
        representations = [
            OUTPUT_IDENTIFIER_MAP.get(output_identifier_type)
            for output_identifier_type in output_identifier_types
        ]
        representations = list(set(representations))
        if None in representations:
            representations.remove(None)
        if not any(representations):
            raise ValueError(
                f"{output_identifier_types} contains invalid identifier types for PubChem."
            )
        if PROPERTY_EXCEPTIONS:
            properties = [
                representation
                for representation in representations
                if representation not in PROPERTY_EXCEPTIONS
            ]
        else:
            properties = representations

        # Search
        input_identifiers_queue = Queue()
        input_identifiers_queue.put(input_identifier.value)
        autocomplete_tried = False
        while not input_identifiers_queue.empty():
            input_value = input_identifiers_queue.get()
            results = await get_properties(
                session,
                properties=properties,
                identifier=input_value,
                namespace=namespace,
                searchtype=None,
            )
            output_identifiers = []
            for representation in representations:
                for result in results:
                    if result and result.get(representation):
                        output_identifiers += [
                            CompoundIdentifier(
                                identifier_type=inverse_map(OUTPUT_IDENTIFIER_MAP)[
                                    representation
                                ],
                                value=str(result[representation]),
                            )
                        ]

            # Autocomplete if search fails
            if (
                len(output_identifiers) == 0
                and self.autocomplete
                and not autocomplete_tried
            ):
                names = await autocomplete(
                    session, input_value, limit=self.autocomplete_limit
                )
                for name in names:
                    input_identifiers_queue.put(name)

        return output_identifiers


async def get_properties(
    session: ClientSession,
    properties,
    identifier,
    namespace="cid",
    searchtype=None,
    **kwargs,
):
    """Retrieve the specified properties from PubChem.

    :param identifier: The compound, substance or assay identifier to use as a search query.
    :param namespace: (optional) The identifier type.
    :param searchtype: (optional) The advanced search type, one of substructure, superstructure or similarity.
    :param as_dataframe: (optional) Automatically extract the properties into a pandas :class:`~pandas.DataFrame`.
    """
    if isinstance(properties, text_types):
        properties = properties.split(",")
    properties = ",".join([PROPERTY_MAP.get(p, p) for p in properties])
    properties = "property/%s" % properties
    # properties += ",Title"
    # print(properties)
    try:
        results = await request(
            session=session,
            api_base=API_BASE,
            identifier=identifier,
            namespace=namespace,
            domain="compound",
            operation=properties,
            output="JSON",
            searchtype=searchtype,
            **kwargs,
        )
    except HTTPNotFound:
        return []
    print(results)
    if results is not None:
        logger.debug(results)
        return results["PropertyTable"]["Properties"]
    else:
        return []


async def autocomplete(
    session: ClientSession,
    identifier: str,
    lookup_dictionary: Optional[str] = "compound",
    **kwargs,
):
    # Call the autocomplete url
    try:
        results = await request(
            session=session,
            api_base=AUTOCOMPLETE_BASE,
            identifier=identifier,
            namespace=None,
            domain=lookup_dictionary,
            operation=identifier,
            output="JSON",
            searchtype=None,
            **kwargs,
        )
    except HTTPNotFound:
        return []

    if results.get("dictionary_terms"):
        logger.debug(results)
        return results["dictionary_terms"]["compound"]
    else:
        return []


async def request(
    session: ClientSession,
    api_base: str,
    identifier,
    namespace="cid",
    domain="compound",
    operation=None,
    output="JSON",
    searchtype=None,
    **kwargs,
):
    """
    Construct API request from parameters and return the response.

    Full specification at http://pubchem.ncbi.nlm.nih.gov/pug_rest/PUG_REST.html
    """
    if not identifier:
        raise ValueError("identifier/cid cannot be None")
    # If identifier is a list, join with commas into string
    if isinstance(identifier, int):
        identifier = str(identifier)
    if not isinstance(identifier, text_types):
        identifier = ",".join(str(x) for x in identifier)
    # Filter None values from kwargs
    kwargs = dict((k, v) for k, v in kwargs.items() if v is not None)
    # Build API URL
    urlid, postdata = None, {}
    if namespace == "sourceid":
        identifier = identifier.replace("/", ".")
    if (
        namespace in ["listkey", "formula", "sourceid"]
        or searchtype == "xref"
        or (searchtype and namespace == "cid")
        or domain == "sources"
    ):
        urlid = quote(identifier.encode("utf8"))
    else:
        # postdata = urlencode([(namespace, identifier)]).encode("utf8")
        postdata = {namespace: identifier}
    comps = filter(
        None, [api_base, domain, searchtype, namespace, urlid, operation, output]
    )
    apiurl = "/".join(comps)
    if kwargs:
        apiurl += "?%s" % urlencode(kwargs)
    # print(apiurl)
    # print(postdata)
    # Make request
    logger.debug("Request URL: %s", apiurl)
    logger.debug("Request data: %s", postdata)
    async with session.post(apiurl, data=postdata) as resp:
        response = await resp.json()
    if response.get("Fault"):
        code = response["Fault"]["Code"]
        if code == "PUGREST.BadRequest":
            raise HTTPBadRequest(reason=code)
        elif code == "PUGREST.NotFound":
            raise HTTPNotFound(reason=code)
        elif code == "PUGREST.NotAllowed":
            raise HTTPMethodNotAllowed(reason=code)
        elif code == "PUGREST.Timeout":
            raise HTTPGatewayTimeout(reason=code)
        elif code == "PUGREST.ServerBusy":
            raise HTTPServiceUnavailable(reason=code)
        elif code == "PUGREST.Unimplemented":
            raise HTTPNotImplemented(reason=code)
        elif code == "PUGREST.ServerError" or code == "PUGREST.Unknown":
            raise HTTPInternalServerError()
    return response
