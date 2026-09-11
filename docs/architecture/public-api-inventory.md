# Experimental public API inventory

All 12 interfaces in the initial contract preview are classified as **proposed open-core**. They are experimental throughout 0.x and are not supported runtime APIs.

| Interface | Kind | Classification |
| --- | --- | --- |
| `memoriesql.__version__` | Python | proposed open-core |
| `memoriesql.contracts.CATALOG_KINDS` | Python | proposed open-core |
| `memoriesql.contracts.CatalogNotFoundError` | Python | proposed open-core |
| `memoriesql.contracts.ContractNotFoundError` | Python | proposed open-core |
| `memoriesql.contracts.available_catalogs` | Python | proposed open-core |
| `memoriesql.contracts.load_catalog` | Python | proposed open-core |
| `memoriesql.contracts.iter_contracts` | Python | proposed open-core |
| `memoriesql.contracts.get_contract` | Python | proposed open-core |
| `memoriesql.contracts.contract_inventory` | Python | proposed open-core |
| `memoriesql --version` | CLI | proposed open-core |
| `memoriesql contracts` | CLI | proposed open-core |
| `memoriesql contract` | CLI | proposed open-core |

Each of the 51 packaged contract records is separately and explicitly classified `proposed_open_core` in `contracts/public-registry.json`. No packaged record or API is classified as proprietary product material or owner-decision-required.
