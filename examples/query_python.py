"""Querying the text base from Python / Jupyter. Run from the project folder: python examples/query_python.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pandas as pd

from rctb.search import TextBase

tb = TextBase("data/out", config="config.yaml")
pd.set_option("display.width", 200, "display.max_colwidth", 70)

# 1. Everything is a dataframe
docs = tb.documents
print(docs.groupby("genre").agg(documents=("doc_id", "count"), pages=("n_pages", "sum")), "\n")

# 2. Keyword search (BM25); filters: club, genre, file_number, country, year_from, year_to
print(tb.keyword("exchange rate", k=5, genre="correspondence")[["doc_id", "date_produced", "snippet"]], "\n")

# 3. Passage search by similarity (a multilingual embedding model makes this cross-lingual)
print(tb.semantic("Japanese members absent from meetings", k=3)[["doc_id", "pages", "score", "text"]], "\n")

# 4. Statistics: documents per year and genre
print(docs.pivot_table(index="year", columns="genre", values="doc_id", aggfunc="count", fill_value=0), "\n")

# 5. Network: persons co-mentioned in the same document -> edge list (for networkx / igraph / Gephi)
persons = tb.sql("SELECT DISTINCT doc_id, canonical AS name FROM entities WHERE type = 'persons'")  # canonical = variants merged
edges = (persons.merge(persons, on="doc_id").query("name_x < name_y")
         .groupby(["name_x", "name_y"]).size().reset_index(name="weight").sort_values("weight", ascending=False))
print(edges.head(10), "\n")

# 6. Space: places of origin and destination of letters (to be geocoded for mapping)
print(tb.sql("""SELECT place_origin, place_destination, count(*) AS n FROM documents
                WHERE genre = 'correspondence' GROUP BY 1, 2 ORDER BY n DESC"""), "\n")

# 7. Plain SQL across tables: every document mentioning an organisation
print(tb.sql("""SELECT d.doc_id, d.date_produced, d.title FROM entities e JOIN documents d USING (doc_id)
                WHERE e.type = 'organizations' AND e.name LIKE '%Specie Bank%' ORDER BY d.date_produced"""))

# 8. RAG (needs llm.backend set in config.yaml):
# r = tb.ask("Why did the Harbin club pay less than its full dues in 1936-38?", k=5, language="zh")
# print(r["answer"]); print(r["citations"])
