/* HTML が本文の正本です。JavaScript は目次・現在地・印刷だけを補助します。 */
(() => {
  const slides = [...document.querySelectorAll("main > .slide")];
  const toc = document.querySelector("[data-toc]");
  const navigation = document.querySelector("#study-navigation");
  const menu = document.querySelector(".menu-button");
  const print = document.querySelector("[data-print]");
  const progress = document.querySelector(".reading-progress");
  const header = document.querySelector(".topbar");

  if (!slides.length) return;
  document.documentElement.classList.add("has-js");
  const pad = (number) => String(number).padStart(2, "0");

  const items = slides.map((slide, index) => {
    const item = document.createElement("li");
    const link = document.createElement("a");
    const number = document.createElement("span");
    number.className = "toc-number";
    number.textContent = pad(index + 1);
    link.href = `#${slide.id}`;
    link.append(number, slide.dataset.navTitle || slide.querySelector("h1, h2").textContent);
    item.append(link);
    const pageNumber = slide.querySelector(".page-number");
    if (pageNumber) pageNumber.textContent = `${pad(index + 1)} / ${pad(slides.length)}`;
    return { item, link, slide };
  });
  if (toc) {
    const entries = [];
    const groups = new Map();
    for (const entry of items) {
      const title = entry.slide.dataset.tocGroup;
      if (!title) {
        entries.push(entry.item);
        continue;
      }
      let group = groups.get(title);
      if (!group) {
        const wrapper = document.createElement("li");
        wrapper.className = "toc-group";
        const details = document.createElement("details");
        const summary = document.createElement("summary");
        summary.textContent = title;
        const list = document.createElement("ol");
        list.className = "toc-sublist";
        details.append(summary, list);
        wrapper.append(details);
        group = { details, list };
        groups.set(title, group);
        entries.push(wrapper);
      }
      group.list.append(entry.item);
      entry.group = group.details;
    }
    toc.replaceChildren(...entries);
  }
  document.querySelectorAll("[data-total]").forEach((element) => {
    element.textContent = slides.length;
  });

  const closeMenu = () => {
    navigation?.classList.remove("is-open");
    menu?.setAttribute("aria-expanded", "false");
  };

  if (menu && navigation) {
    menu.hidden = false;
    menu.addEventListener("click", () => {
      const isOpen = navigation.classList.toggle("is-open");
      menu.setAttribute("aria-expanded", String(isOpen));
    });
    navigation.addEventListener("click", (event) => {
      const link = event.target.closest("a");
      if (!link || window.innerWidth > 960) return;
      closeMenu();
      // 目次を閉じた後は移動先へフォーカスを渡します。
      const destination = document.getElementById(decodeURIComponent(link.hash.slice(1)));
      if (destination) {
        destination.tabIndex = -1;
        destination.focus({ preventScroll: true });
      }
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && navigation.classList.contains("is-open")) {
        closeMenu();
        menu.focus();
      }
    });
    document.addEventListener("click", (event) => {
      if (!navigation.contains(event.target) && !menu.contains(event.target)) closeMenu();
    });
    window.matchMedia("(max-width: 960px)").addEventListener("change", closeMenu);
  }

  if (print) {
    print.hidden = false;
    print.addEventListener("click", () => window.print());
  }

  let scheduled = false;
  let previousCurrent;
  const updatePosition = () => {
    const threshold = (header?.offsetHeight || 68) + Math.min(window.innerHeight * 0.3, 220);
    let current = items[0];
    for (const item of items) {
      if (item.slide.getBoundingClientRect().top <= threshold) current = item;
    }
    // 文書末尾では、最後のページが画面上部に届かない場合も現在地に反映します。
    const scrollable = document.documentElement.scrollHeight - window.innerHeight;
    if (scrollable > 0 && window.scrollY >= scrollable - 2) current = items.at(-1);
    items.forEach(({ link }) => {
      if (link === current.link) link.setAttribute("aria-current", "location");
      else link.removeAttribute("aria-current");
    });
    if (current !== previousCurrent) {
      if (current.group) current.group.open = true;
      previousCurrent = current;
    }
    if (progress) {
      const amount = scrollable > 0 ? Math.max(0, Math.min(1, window.scrollY / scrollable)) : 1;
      progress.style.transform = `scaleX(${amount})`;
    }
    scheduled = false;
  };
  const scheduleUpdate = () => {
    if (scheduled) return;
    scheduled = true;
    window.requestAnimationFrame(updatePosition);
  };
  window.addEventListener("scroll", scheduleUpdate, { passive: true });
  window.addEventListener("resize", scheduleUpdate);
  window.addEventListener("load", scheduleUpdate);
  document.querySelectorAll("main details").forEach((details) => {
    details.addEventListener("toggle", scheduleUpdate);
  });
  updatePosition();
})();
