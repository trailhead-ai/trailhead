var marker = document.createElement("div");
marker.id = "made-by-script";
document.body.appendChild(marker);
document.fonts.load('32px ProbeFont').then(function () {
  document.documentElement.setAttribute("data-pad-ready", "");
});
