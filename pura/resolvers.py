from pura.compound import (
    CompoundIdentifier,
    CompoundIdentifierType,
    Compound,
    standardize_identifier,
)
from pura.services import Service, CIR, PubChem, ChemSpider
from tqdm import tqdm
from aiohttp import *
from aiohttp.web_exceptions import (
    HTTPClientError,
    HTTPServerError,
    HTTPServiceUnavailable,
)
import asyncio
from typing import Optional, List, Union
from itertools import combinations
from functools import reduce
import logging

logger = logging.getLogger(__name__)

aiohttp_errors = (
    HTTPServiceUnavailable,
    ClientConnectionError,
    TimeoutError,
    ClientConnectorCertificateError,
    ClientConnectorError,
    ClientConnectorSSLError,
    ClientError,
    ClientHttpProxyError,
    ClientOSError,
    ClientPayloadError,
    ClientProxyConnectionError,
    ClientResponseError,
    ClientSSLError,
    ContentTypeError,
    InvalidURL,
    ServerConnectionError,
    ServerDisconnectedError,
    ServerFingerprintMismatch,
    ServerTimeoutError,
    WSServerHandshakeError,
    asyncio.TimeoutError,
)


class CompoundResolver:
    """Resolve compound identifier types using external services such as PubChem.

    Parameters
    ----------
    services : list of Service
        The services used for resolution.
    silent : bool, optional
        If True, logs errors but does not raise them. Default is False

    Examples
    --------


    """

    def __init__(self, services: List[Service], silent: Optional[bool] = False):
        self._services = services
        self.silent = silent

    def resolve(
        self,
        input_identifiers: List[CompoundIdentifier],
        output_identifier_type: List[CompoundIdentifierType],
        agreement: Optional[int] = 1,
        batch_size: Optional[int] = None,
    ) -> List[List[CompoundIdentifier]]:
        """Resolve a list of compound identifiers to another identifier type(s).

        Arguments
        ---------
        input_identifiers : List[CompoundIdentifier]
            The list of compound identifiers that should be resolved
        output_identifiers_types: List[CompoundIdentifierType]
            The list of compound identifier types to resolve to
        agreement : int, optional
            The number of services that must give the same resolved
            compoundidentifier for the resolution to be considered correct.
            Default is 1.
        batch_size : int, optional
            The batch size sets the number of requests to send simultaneously.
            Defaults to 100 or the length input_idententifier, whichever is smaller.
        """
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError as e:
            if str(e).startswith("There is no current event loop in thread"):
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            else:
                raise
        logging.info("Running download")
        return loop.run_until_complete(
            self._resolve(
                input_identifiers=input_identifiers,
                output_identifier_type=output_identifier_type,
                agreement=agreement,
                batch_size=batch_size,
            )
        )

    async def _resolve(
        self,
        input_identifiers: List[CompoundIdentifier],
        output_identifier_type: CompoundIdentifierType,
        agreement: Optional[int] = 1,
        batch_size: Optional[int] = None,
    ) -> List[List[Union[CompoundIdentifier, None]]]:
        """Resolve a list of compound identifiers to another identifier type(s).

        Arguments
        ---------
        input_identifiers : List[CompoundIdentifier]
            The list of compound identifiers that should be resolved
        output_identifiers_types: List[CompoundIdentifierType]
            The list of compound identifier types to resolve to
        agreement : int, optional
            The number of services that must give the same resolved
            compoundidentifier for the resolution to be considered correct.
            Default is 1.
        batch_size : int, optional
            The batch size sets the number of requests to send simultaneously.
            Defaults to 100 or the length input_idententifier, whichever is smaller.

        """

        n_identifiers = len(input_identifiers)
        if batch_size is None:
            batch_size = 100 if n_identifiers >= 100 else n_identifiers
        n_batches = n_identifiers // batch_size
        n_batches += 0 if n_identifiers % batch_size == 0 else 1
        resolved_identifiers = []
        # Iterate through batches
        for batch in tqdm(range(n_batches), position=0, desc="Batch"):
            # Get subset of data
            start = batch * batch_size
            batch_identifiers = input_identifiers[start : start + batch_size]

            # Start aiohttp session
            async with ClientSession() as session:
                # Create series of tasks to run in parallel
                tasks = [
                    self.resolve_one_identifier(
                        session,
                        compound_identifier,
                        output_identifier_type,
                        agreement,
                        n_retries=7,
                    )
                    for compound_identifier in batch_identifiers
                ]
                batch_bar = tqdm(
                    asyncio.as_completed(tasks),
                    total=len(tasks),
                    desc=f"Batch {batch} Progress",
                    position=1,
                    leave=True,
                )
                resolved_identifiers.extend([await f for f in batch_bar])
                batch_bar.clear()

        return resolved_identifiers

    async def resolve_one_identifier(
        self,
        session: ClientSession,
        input_identifier: CompoundIdentifier,
        output_identifier_type: CompoundIdentifierType,
        agreement: int,
        n_retries: Optional[int] = 7,
    ) -> Union[List[CompoundIdentifier], None]:

        agreement_count = 0
        resolved_identifiers_list = []
        for i, service in enumerate(self._services):
            for j in range(n_retries):
                try:
                    resolved_identifiers = await service.resolve_compound(
                        session,
                        input_identifier=input_identifier,
                        output_identifier_type=output_identifier_type,
                    )
                    # Standardize identifiers (e.g., SMILES canonicalization)
                    for identifier in resolved_identifiers:
                        if identifier is not None:
                            standardize_identifier(identifier)
                    resolved_identifiers_list.append(resolved_identifiers)
                    break
                except aiohttp_errors:
                    # If server is busy, use exponential backoff
                    logger.debug(f"Sleeping for {2**j}")
                    await asyncio.sleep(2**j)
                except (HTTPClientError, HTTPServerError) as e:
                    # Log/raise on all other HTTP errors
                    if self.silent:
                        logger.error(e)
                        return
                    else:
                        raise e

            # Chceck agreement between services
            if i > 0 and len(resolved_identifiers_list) > 0:
                resolved_identifiers = self.reduce_options(
                    resolved_identifiers_list, agreement
                )
                agreement_count += 1
            elif len(resolved_identifiers_list) > 0:
                agreement_count += 1
            if agreement_count >= agreement:
                break

        if agreement_count < agreement:
            error_txt = f"Not sufficient agreeement ({agreement_count}) for {resolved_identifiers_list}"
            if self.silent:
                logger.error(error_txt)
                return
            else:
                raise TypeError(error_txt)

        return resolved_identifiers

    def reduce_options(
        self, identifiers_list: List[List[CompoundIdentifier]], agreement: int
    ) -> List[CompoundIdentifier]:
        """
        Reduce and deduplcate options (this is the agreement algorithm)

        Notes
        ------
        Algorithm:
        1. Find all combinatons of services that can satisfy agreement (combinations)
        2. Find the intersection of each combination
        3. If the intersection is greater than zero, then you have sufficient agreement.
        """
        identifiers_list_new = []
        for identifiers in identifiers_list:
            if len(identifiers) > 0:
                identifiers_list_new.append(
                    [identifier.value for identifier in identifiers]
                )
                identifier_type = identifiers[0].identifier_type
        identifiers_sets = [set(ident) for ident in identifiers_list_new]
        options = list(range(len(identifiers_list_new)))
        intersection = []
        for combo in combinations(options, agreement):
            intersection = reduce(
                set.intersection, [identifiers_sets[combo_i] for combo_i in combo]
            )
            if len(intersection) > 0:
                break
        if len(intersection) == 0:
            return []
        else:
            return [
                CompoundIdentifier(identifier_type=identifier_type, value=identifier)
                for identifier in intersection
            ]


def resolve_names(
    names: List[str],
    output_identifier_type: CompoundIdentifierType,
    agreement: int = 1,
    batch_size: int = 100,
    services: Optional[List[Service]] = None,
    silent: Optional[bool] = False,
) -> List[CompoundIdentifier]:
    """Resolve a list of names to an identifier type.

    Arguments
    ---------
    names : list of str
        The list of compound names that should be resolved
    output_identifiers_type : CompoundIdentifierType
        The list of compound identifier types to resolve to
    agreement : int, optional
        The number of services that must give the same resolved
        compoundidentifier for the resolution to be considered correct.
        Default is 1.
    batch_size : int, optional
        The batch size sets the number of requests to send simultaneously.
        Defaults to 100 or the length input_idententifier, whichever is smaller.
    services : list of `Service`, optional
        Services used to do resolution. Defaults to PubChem and CIR.
    silent : bool, optional
        If True, logs errors but does not raise them. Default is False

    Example
    -------
    >>> from pura.services import  Pubchem, CIR
    >>> smiles = resolve_names(
    ...     ["aspirin", "ibuprofen", "toluene"],
    ...     output_identifier_type=CompoundIdentifierType.SMILES,
    ...     services=[Pubchem(), CIR()],
    ...     agreement=2,
    ... )

    """
    if services is None:
        services = [PubChem(), CIR()]
    name_identifiers = [
        CompoundIdentifier(identifier_type=CompoundIdentifierType.NAME, value=name)
        for name in names
    ]
    resolver = CompoundResolver(services=services, silent=silent)
    return resolver.resolve(
        input_identifiers=name_identifiers,
        output_identifier_type=output_identifier_type,
        agreement=agreement,
        batch_size=batch_size,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    smiles = resolve_names(
        ["aspirin", "ibuprofen", "[Ru(p-cymene)I2]2"],
        output_identifier_type=CompoundIdentifierType.SMILES,
        services=[PubChem(), CIR(), ChemSpider()],
        agreement=2,
        batch_size=10,
    )
    import pprint

    pp = pprint.PrettyPrinter(indent=4)
    pp.pprint(smiles)
