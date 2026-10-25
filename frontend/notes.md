db password: jkFajULnLbuxLc3n

We need to improve the search box.

For one, the search should support taxonomical searches. Here's how this should work: your search may consist of many optional taxonomical specifiers, separated by commas. For instance, a search could contain "family=Aceraceae", which would find all the plants in Aceraceae. Once this is typed, it should turn into one of those boxes with an x (to remove it). There's a specifier for each colum in the plant_taxonomy table (except for plant_id and lineage), including the common names. So, a full search might look something like:

[x family=Aceraceae] purple

Which would find all plants in Aceraceae with the scientific or common name purple.

Also, there should be autocomplete, for all of these. When you type in the search box, it should list below a set of suggestions which match the current search query. There can be suggestions for common names, scientific names, or for any taxonomical specifier. Ideally, the order should be: any tax. specifier, common name, and then scientific name. Clicking the suggestion will accept it (or pressing enter will accept the first (most relevant) one).
