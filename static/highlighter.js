/* WeBook: выделение цитат в HTML-контейнере (статья, FB2).
   Использование:
     WebookHighlighter.init({ container: el, resourceType: 'link', resourceId: 42 });
   Сохраняет цитату с символьными смещениями внутри текста контейнера
   (location = "start-end"), восстанавливает <mark> при загрузке. */
window.WebookHighlighter = (function () {
  function textNodes(root) {
    var nodes = [], w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
    var n; while ((n = w.nextNode())) nodes.push(n);
    return nodes;
  }
  function offsetOf(root, node, off) {
    var nodes = textNodes(root), total = 0;
    for (var i = 0; i < nodes.length; i++) {
      if (nodes[i] === node) return total + off;
      total += nodes[i].textContent.length;
    }
    return total;
  }
  function rangeFromOffsets(root, start, end) {
    var nodes = textNodes(root), total = 0, range = document.createRange(), set = false;
    for (var i = 0; i < nodes.length; i++) {
      var len = nodes[i].textContent.length;
      if (!set && total + len >= start) { range.setStart(nodes[i], start - total); set = true; }
      if (set && total + len >= end) { range.setEnd(nodes[i], end - total); return range; }
      total += len;
    }
    return null;
  }
  function wrap(range, color) {
    try {
      var m = document.createElement('mark');
      m.className = 'wb-hl';
      if (color) m.style.backgroundColor = color;
      range.surroundContents(m);
      return true;
    } catch (e) { return false; }  // пересечение тегов — визуально пропускаем
  }

  function init(opts) {
    var root = opts.container;
    if (!root) return;
    var rtype = opts.resourceType, rid = opts.resourceId;

    // плавающая кнопка
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn btn-sm btn-warning shadow';
    btn.innerHTML = '<i class="bi bi-quote"></i> Цитата';
    btn.style.cssText = 'position:absolute;z-index:1060;display:none';
    document.body.appendChild(btn);

    var pending = null;  // {start,end,quote}

    function hideBtn() { btn.style.display = 'none'; pending = null; }

    root.addEventListener('mouseup', function () {
      setTimeout(function () {
        var sel = window.getSelection();
        if (!sel || sel.isCollapsed) { hideBtn(); return; }
        var text = sel.toString().trim();
        if (text.length < 2) { hideBtn(); return; }
        var r = sel.getRangeAt(0);
        if (!root.contains(r.commonAncestorContainer)) { hideBtn(); return; }
        var start = offsetOf(root, r.startContainer, r.startOffset);
        var end = offsetOf(root, r.endContainer, r.endOffset);
        if (end < start) { var t = start; start = end; end = t; }
        pending = { start: start, end: end, quote: text };
        var rect = r.getBoundingClientRect();
        btn.style.top = (window.scrollY + rect.top - 38) + 'px';
        btn.style.left = (window.scrollX + rect.left) + 'px';
        btn.style.display = '';
      }, 10);
    });

    document.addEventListener('mousedown', function (e) {
      if (e.target !== btn) hideBtn();
    });

    btn.addEventListener('click', function () {
      if (!pending) return;
      var loc = pending.start + '-' + pending.end;
      fetch('/highlights', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ resource_type: rtype, resource_id: rid, quote: pending.quote, location: loc })
      }).then(function (r) { return r.ok ? r.json() : null; })
        .then(function () {
          var rng = rangeFromOffsets(root, pending.start, pending.end);
          if (rng) wrap(rng);
          window.getSelection().removeAllRanges();
          hideBtn();
        }).catch(function () { hideBtn(); });
    });

    // восстановление сохранённых цитат
    fetch('/highlights/list?resource_type=' + rtype + '&resource_id=' + rid)
      .then(function (r) { return r.ok ? r.json() : []; })
      .then(function (list) {
        list.forEach(function (h) {
          if (!h.location || h.location.indexOf('-') < 0) return;
          var parts = h.location.split('-');
          var s = parseInt(parts[0], 10), e = parseInt(parts[1], 10);
          if (isNaN(s) || isNaN(e)) return;
          var rng = rangeFromOffsets(root, s, e);
          if (rng) wrap(rng);
        });
      }).catch(function () {});
  }

  return { init: init };
})();
