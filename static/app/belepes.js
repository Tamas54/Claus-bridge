// A belépőlink megerősítő lapja: a tokent POST-tal használjuk fel (a link-előnézők GET-je nem fogyasztja el).
// Böngészőben azonnal elküldjük; ha valamiért nem megy, a „Belépek” gomb ott marad.
document.getElementById('belepes-urlap')?.submit();
