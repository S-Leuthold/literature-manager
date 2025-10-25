# Literature Manager - Topic Taxonomy (DRAFT)

This is a proposed fixed topic list for categorizing papers. Review and modify as needed.

## Soil Carbon & Organic Matter (8 topics)

- **soil-carbon** - General soil carbon cycling, storage, sequestration
- **soil-organic-matter** - Broader SOM dynamics, chemistry, transformation
- **maom** - Mineral-associated organic matter specifically
- **pom** - Particulate organic matter specifically
- **aggregates** - Soil aggregation, aggregate-associated carbon
- **dissolved-organic-matter** - DOC/DOM dynamics
- **pyrogenic-carbon** - Biochar, charcoal, black carbon
- **priming-effects** - Priming, co-metabolism, SOC destabilization

## Analytical Methods (5 topics)

- **soil-spectroscopy** - FTIR, MIR, NIR, Raman spectroscopy
- **soil-fractionation** - Density, size, chemical fractionation methods
- **isotope-methods** - Isotope labeling, tracing, natural abundance
- **molecular-methods** - Pyrolysis, NMR, mass spectrometry for OM characterization
- **remote-sensing** - Satellite, aerial, proximal sensing for soil properties

## Biogeochemical Processes (6 topics)

- **microbial-processes** - Microbial ecology, enzyme activity, microbial C cycling
- **nitrogen-cycling** - N mineralization, immobilization, nitrification, denitrification
- **nutrient-cycling** - P, K, S, micronutrient cycling (not N-specific)
- **litter-decomposition** - Plant litter breakdown, residue decomposition
- **rhizosphere-processes** - Root-soil interactions, rhizodeposition, mycorrhizae
- **soil-respiration** - CO2 efflux, heterotrophic respiration, soil metabolism

## Agricultural Systems (8 topics)

- **tillage** - Tillage effects, no-till, conservation tillage
- **cover-crops** - Cover crop benefits, species selection, management
- **crop-rotation** - Rotation effects on soil and yield
- **organic-agriculture** - Organic farming systems, organic amendments
- **precision-agriculture** - Variable rate, yield mapping, site-specific management
- **soil-amendments** - Compost, manure, biosolids, mineral amendments (not biochar)
- **cropping-intensity** - Continuous vs. rotational, fallow, double-cropping
- **irrigation** - Water management, drip, flood, fertigation effects on soil

## Environmental & Climate (5 topics)

- **climate-change** - Climate impacts on soils, adaptation, mitigation
- **soil-erosion** - Water/wind erosion, soil loss, conservation practices
- **land-use-change** - Conversion effects (forest→ag, grassland→cropland, etc.)
- **wetland-soils** - Wetland biogeochemistry, hydric soils, drainage
- **fire-ecology** - Wildfire effects on soils, prescribed burning

## Soil Properties & Processes (4 topics)

- **soil-texture** - Clay, silt, sand effects; texture-dependent processes
- **soil-moisture** - Water retention, drought, flooding effects on biogeochemistry
- **soil-temperature** - Temperature sensitivity, seasonal dynamics, warming
- **redox-processes** - Reduction-oxidation, anaerobic processes, electron acceptors

## Social Science & Economics (4 topics)

- **farmer-adoption** - Adoption barriers/drivers, behavioral economics, decision-making
- **agricultural-policy** - Policy impacts, incentives, regulations, programs
- **agricultural-economics** - Cost-benefit, profitability, market dynamics
- **ecosystem-services** - Valuation, payment schemes, multi-functionality

## Total: 40 topics

---

## Multi-Topic Papers

Papers can be assigned to 2 topics (rarely 3) when:
1. **Method + Substance**: Paper uses specialized method to study a process
   - Example: FTIR study of MAOM → `soil-spectroscopy` + `maom`
2. **Two Equal Foci**: Paper truly covers two research areas equally
   - Example: Cover crops and nitrogen cycling → `cover-crops` + `nitrogen-cycling`

## Review Queue

Papers that don't fit any topic well should be flagged for review with suggested new topic.

---

## Notes for Refinement

**Questions for you:**
1. Are there redundancies? (e.g., is `soil-carbon` too broad alongside MAOM/POM?)
2. Missing topics in your research area?
3. Too granular anywhere? (Should we collapse some?)
4. Topic names intuitive for your workflow?

**Known edge cases from current library:**
- "Disaster-driven discussion" - might need `research-methods` or `scientific-communication`?
- Agroecology papers - fit under `organic-agriculture` or need separate `agroecology` topic?
- Soil health/quality - implicit in many topics or needs its own?

---

**Next Steps:**
1. Review and edit this list
2. Decide on topic descriptions (brief definitions for borderline cases)
3. Implement in code as fixed taxonomy
4. Rewrite LLM prompt to select from this list
