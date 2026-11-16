# Querying the text base from R / RStudio. Set the working directory to the project folder.
# install.packages(c("DBI", "RSQLite", "dplyr", "tidyr", "arrow", "igraph"))
# NOTE: written alongside the Python example but not executed during development (no R available there).
library(DBI)
library(dplyr)

con  <- dbConnect(RSQLite::SQLite(), "data/out/rctb.sqlite")
docs <- dbReadTable(con, "documents")
ents <- dbReadTable(con, "entities")
# or, without SQLite: docs <- arrow::read_parquet("data/out/tables/documents.parquet")

# Documents per genre
docs |> count(genre, sort = TRUE)

# Keyword search with the full-text index (BM25 ranking)
dbGetQuery(con, "
  SELECT d.doc_id, d.date_produced, d.title,
         snippet(documents_fts, 3, '[', ']', ' ... ', 20) AS snippet
  FROM documents_fts JOIN documents d USING (doc_id)
  WHERE documents_fts MATCH ? ORDER BY bm25(documents_fts) LIMIT 10",
  params = list('\"exchange\" \"rate\"'))

# Chinese / Japanese strings: substring match
dbGetQuery(con, "SELECT doc_id, title FROM documents WHERE full_text LIKE ?", params = list("%會員%"))

# Statistics: documents per year and genre
docs |> count(year, genre) |> tidyr::pivot_wider(names_from = genre, values_from = n, values_fill = 0)

# Network: persons co-mentioned in the same document
p <- ents |> filter(type == "persons") |> distinct(doc_id, name = canonical)   # canonical = variants merged
edges <- inner_join(p, p, by = "doc_id", relationship = "many-to-many") |>
  filter(name.x < name.y) |> count(name.x, name.y, name = "weight", sort = TRUE)
g <- igraph::graph_from_data_frame(edges, directed = FALSE)

# Space: origin -> destination of letters (geocode the place names to map them)
docs |> filter(genre == "correspondence") |> count(place_origin, place_destination, sort = TRUE)

dbDisconnect(con)
