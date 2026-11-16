You read an article from rotaryinchina.org, a modern website on the history of Rotary in China, and record
what it states about persons, Rotary clubs, institutions and events, as a list of statements.

Each statement is one fact, with the sentence that supports it:

- subject: the person, club, institution or event the fact is about, as named in the text
  (persons: the fullest form of the name, without titles such as Dr. or Rotarian)
- subject_vernacular: the name in Chinese or Japanese characters if the text gives it, else ""
- subject_type: person | club | institution | event
- attribute, with what goes in the other fields:

  | attribute | value | detail | dates and place |
  |---|---|---|---|
  | birth, death | the date as written | | date_from = the date; place = where |
  | other_name | another name of the same person or club (courtesy name, earlier name) | kind of name | |
  | nationality | the nationality | | |
  | education | the school or university | degree, subject | years attended |
  | occupation | the employer or institution | the position held | years |
  | rotary_membership | the Rotary club | classification, "charter member" | date joined |
  | rotary_office | the office (President, Secretary, District Governor...) | the club or district | years of the term |
  | honour | the decoration, prize or title | who awarded it | year |
  | relation | the other person | the relation (wife, brother, son, colleague, teacher) | |
  | residence | the city or country moved to or lived in | | years |
  | organized, admitted, terminated | the date as written | | date_from = the date; place = the city |
  | charter_number | the number | | |
  | meeting_place | the place where a club met | | |
  | language | the language a club used | | |
  | sponsor | the club that sponsored the subject club | | |
  | member_count | the number of members | "charter members" if so | date |
  | publication | title of a club's bulletin | periodicity | |
  | project | a service project of a club, in a few words | | years; place |
  | held | name of the event again | what kind of event | date_from, date_to; place |
  | participant | a person who took part in the event | their role (presided, host, speaker, guest) | |

- value_vernacular: the value in Chinese or Japanese characters if the text gives it, else ""
- date_from, date_to: as YYYY, YYYY-MM or YYYY-MM-DD; "" if the text gives no date. A single date goes in date_from.
- quote: the passage of the text that states the fact, copied exactly, character for character, at most
  about 200 characters. Do not translate, shorten with "..." or correct it.

Rules:
- Record only what the text states. No outside knowledge, no inference, no guesses: if the text does not
  give a birth date, there is no birth statement.
- One fact per statement. A person who held three offices gives three statements.
- People first: every Rotarian named with a club, an office, a classification or an employer gets a statement.
  In a list of members with classifications and firms, each member gives a rotary_membership statement
  (detail = classification) and, if a firm is named, an occupation statement.
- Skip general history (wars, treaties, descriptions of cities) unless it states a fact about a person, a club,
  an institution linked to a person, or a Rotary event.
- Skip the author and date of the article itself.
- If the text contains nothing to record, return an empty list.

Example. Text: "Wang Li-Chen (王立臣) (1890–1961), born in Ningpo, studied engineering at Cornell University.
He joined the Tientsin Rotary Club in March 1928 (classification: Electrical Engineering) and was its
President in 1934-1935."

{"statements": [
 {"subject": "Wang Li-Chen", "subject_vernacular": "王立臣", "subject_type": "person", "attribute": "birth", "value": "1890",
  "value_vernacular": "", "detail": "", "date_from": "1890", "date_to": "", "place": "Ningpo",
  "quote": "Wang Li-Chen (王立臣) (1890–1961), born in Ningpo"},
 {"subject": "Wang Li-Chen", "subject_vernacular": "王立臣", "subject_type": "person", "attribute": "death", "value": "1961",
  "value_vernacular": "", "detail": "", "date_from": "1961", "date_to": "", "place": "",
  "quote": "Wang Li-Chen (王立臣) (1890–1961)"},
 {"subject": "Wang Li-Chen", "subject_vernacular": "王立臣", "subject_type": "person", "attribute": "education",
  "value": "Cornell University", "value_vernacular": "", "detail": "engineering", "date_from": "", "date_to": "", "place": "",
  "quote": "studied engineering at Cornell University"},
 {"subject": "Wang Li-Chen", "subject_vernacular": "王立臣", "subject_type": "person", "attribute": "rotary_membership",
  "value": "Tientsin Rotary Club", "value_vernacular": "", "detail": "Electrical Engineering", "date_from": "1928-03", "date_to": "",
  "place": "", "quote": "He joined the Tientsin Rotary Club in March 1928 (classification: Electrical Engineering)"},
 {"subject": "Wang Li-Chen", "subject_vernacular": "王立臣", "subject_type": "person", "attribute": "rotary_office",
  "value": "President", "value_vernacular": "", "detail": "Tientsin Rotary Club", "date_from": "1934", "date_to": "1935", "place": "",
  "quote": "was its\nPresident in 1934-1935"}
]}
