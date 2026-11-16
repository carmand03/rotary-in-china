You are indexing the named entities in a document from the archives of Rotary clubs in China and Manchuria (1919-1952), for historians who will search for people and trace networks between them. You receive the document's transcribed text (it may contain OCR errors). List the entities it mentions.

People are the priority. Work through the text from top to bottom and list every person named in it, however many there are: authors, addressees, signatories, officers, members in a roster or table, speakers, guests, people mentioned in passing. Names in lists and tables count exactly like names in sentences, so a roster of forty members yields forty persons. A person named only by family name ("Rotarian Sahara", "Mr. Pedersen") still counts.

For each entity give:

- `name`: exactly as written in the text, keeping the document's spelling and form ("Y. Gundsi", "Gundsi, Y.", "Yoshio GUNDSI"). Leave out titles such as Mr., Dr., Rotarian, President.
- `role`: for a person, the office, occupation or affiliation that the text states ("Secretary", "Deputy Commissioner of Customs", "guest speaker, architect"). For other entities, a few words on what it is if the text says so. Null if the text says nothing.
- `normalized`: only for a place that has a well-known modern name different from the one written (Tientsin → Tianjin, Mukden → Shenyang). Null in every other case.

The five types:

- `persons`: individual human beings named in the text.
- `organizations`: named bodies: clubs, companies, banks, government offices, schools, newspapers as organisations, committees with a proper name. "Rotary International" and "Rotary Club of Harbin" are organisations.
- `locations`: countries, regions, cities, streets, buildings and venues named as places ("Harbin", "1 Nagorny prospect", "Nichiman Club" when it is where a meeting is held).
- `events`: only specific, named occasions that happened at a particular time: a convention, a conference, a charter night, a named celebration ("1937 Rotary International Convention, Nice", "Family Night of 24 April 1937"). The club's ordinary weekly meetings, a "meeting", a "visit" or a "talk" are not events. Two occasions in different years or places are two different events: keep the year or place in the name. Many documents contain no event at all.
- `works`: only titled publications and creative works named in the text: a periodical, book, pamphlet, film, a speech or paper cited by its title ("The Rotarian", "The Harbin Rotarian", "News Letter"). Forms, reports, letters, lists and certificates are not works. Many documents contain no work at all.

List each entity once per type, under the form in which it first appears. An empty list is the right answer for a type the text does not contain. Do not add entities from your own knowledge, and do not list the document's genre or its subject as an entity.

Example. Text:

    ROTARY CLUB OF TIENTSIN
    Astor House Hotel, Tientsin, China                      March 4th, 1931
    Mr. Chesley R. Perry, Secretary, Rotary International, Chicago.
    Dear Ches: At our meeting last Thursday Dr. W. H. Chen spoke on flood relief in Hopei, and
    Rotarian Evans reported on the Pacific Rotary Conference held at Tokyo in October.
    We enclose our order for twelve copies of The Rotarian.
    Officers 1931-32:  President  H. K. Li (banking)   Treasurer  Evans, J. D.
                                           Yours sincerely, P. S. Ho, Hon. Secretary

Entities:

    persons: Chesley R. Perry (Secretary, Rotary International); W. H. Chen (speaker); Evans (Rotarian);
             H. K. Li (President 1931-32, banking); Evans, J. D. (Treasurer); P. S. Ho (Hon. Secretary)
    organizations: ROTARY CLUB OF TIENTSIN; Rotary International
    locations: Astor House Hotel; Tientsin (normalized: Tianjin); China; Chicago; Hopei (normalized: Hebei); Tokyo
    events: Pacific Rotary Conference held at Tokyo in October
    works: The Rotarian

Note what the example leaves out: "our meeting last Thursday" is not an event, the "order" is not a work, and "Evans" and "Evans, J. D." are both listed because the text does not say they are the same man.
