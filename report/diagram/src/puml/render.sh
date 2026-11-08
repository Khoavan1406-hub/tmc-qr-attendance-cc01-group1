#!/bin/sh
# Render the class diagrams (Section 4.3) and the data view (Section 9.3) with PlantUML + Graphviz.
# Usage: PLANTUML_JAR=/path/plantuml.jar sh render.sh   (tested with PlantUML 1.2024.7, Graphviz 2.43)
set -e
cd "$(dirname "$0")"
java -DPLANTUML_LIMIT_SIZE=12000 -jar "${PLANTUML_JAR:-plantuml.jar}" -tpng fig4-18-scan-flow.puml fig4-19-signals-intervention.puml fig4-20-domain.puml fig9-1-data-view.puml
mv fig4-18-scan-flow.png ../../class/figure-4-18-scan-flow-classes.png
mv fig4-19-signals-intervention.png ../../class/figure-4-19-signals-intervention-classes.png
mv fig4-20-domain.png ../../class/figure-4-20-gate-core-entities.png
mv fig9-1-data-view.png ../../data/figure-9-1-data-view.png
