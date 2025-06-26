// Run syntax: node convert-slides.js filename.adoc
// Load Asciidoctor.js and the reveal.js converter
var asciidoctor = require('@asciidoctor/core')()
var asciidoctorRevealjs = require('@asciidoctor/reveal.js')
asciidoctorRevealjs.register()

// Convert the document 'presentation.adoc' using the reveal.js converter
var options = { safe: 'safe', backend: 'revealjs' }
asciidoctor.convertFile(process.argv[2], options)
