(function () {
  var SUPABASE_URL = "https://osmvsriosaojgcmgwvhk.supabase.co";
  var SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im9zbXZzcmlvc2FvamdjbWd3dmhrIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODU5MDgzNTYsImV4cCI6MjEwMTQ4NDM1Nn0.hQvs3jYdq0YNmwROJM3p5ijmY7jwGBmwcg5jMPVgXv0";
  var INITIAL_COUNT = 6;
  var endpoint = SUPABASE_URL + "/rest/v1/articles?select=title,url,published_at,created_at&order=created_at.desc";

  var wrapper = document.querySelector(".zrs-news-widget");
  if (!wrapper) return;
  var list = wrapper.querySelector(".zrs-news-list");

  function renderArticle(article) {
    var li = document.createElement("li");
    li.style.cssText = "padding: 0.75rem 0; border-bottom: 1px solid #eee;";
    var a = document.createElement("a");
    a.href = article.url;
    a.textContent = article.title;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.style.cssText = "color: #222; text-decoration: none;";
    li.appendChild(a);
    list.appendChild(li);
  }

  fetch(endpoint, {
    headers: { apikey: SUPABASE_ANON_KEY, Authorization: "Bearer " + SUPABASE_ANON_KEY },
  })
    .then(function (res) { return res.json(); })
    .then(function (articles) {
      list.innerHTML = "";
      if (!Array.isArray(articles) || !articles.length) {
        list.innerHTML = '<li style="padding: 0.75rem 0; color: #888;">まだ記事がありません。</li>';
        return;
      }
      articles.slice(0, INITIAL_COUNT).forEach(renderArticle);

      var rest = articles.slice(INITIAL_COUNT);
      if (rest.length) {
        var moreBtn = document.createElement("button");
        moreBtn.type = "button";
        moreBtn.textContent = "他の記事はこちら(残り" + rest.length + "件)";
        moreBtn.style.cssText = "display:block; width:100%; margin-top:1rem; padding:0.6rem; background:#f5f5f5; border:1px solid #ddd; border-radius:4px; color:#333; font-size:0.9rem; cursor:pointer;";
        moreBtn.addEventListener("click", function () {
          rest.forEach(renderArticle);
          moreBtn.remove();
        });
        wrapper.appendChild(moreBtn);
      }
    })
    .catch(function () {
      list.innerHTML = '<li style="padding: 0.75rem 0; color: #888;">記事一覧の読み込みに失敗しました。</li>';
    });
})();
