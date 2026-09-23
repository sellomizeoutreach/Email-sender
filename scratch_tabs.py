    @gather_metrics("tabs")
    def tabs(
        self,
        tabs: Sequence[str],
        *,
        width: WidthWithoutContent = "stretch",
        height: Height = "content",
        default: str | None = None,
        key: Key | None = None,
        on_change: Literal["ignore", "rerun"] | WidgetCallback = "ignore",
        args: WidgetArgs | None = None,
        kwargs: WidgetKwargs | None = None,
    ) -> Sequence[TabContainer]:
        r"""Insert containers separated into tabs.

        Inserts a number of multi-element containers as tabs.
        Tabs are a navigational element that allows users to easily
        move between groups of related content.

        To add elements to the returned containers, you can use the ``with`` notation
        (preferred) or just call methods directly on the returned object. See
        the examples below.

        By default, all tab content is computed and sent to the frontend
        regardless of which tab is selected. To enable lazy execution where
        only the selected tab's content runs, use ``on_change="rerun"`` or
        pass a callable to ``on_change``. Each tab's ``.open`` property
        indicates whether it is the currently selected tab, letting you
        conditionally render expensive content.

        Parameters
        ----------
        tabs : list of str
            Creates a tab for each string in the list. The first tab is selected
            by default. The string is used as the name of the tab and can
            optionally contain GitHub-flavored Markdown of the following types:
            Bold, Italics, Strikethroughs, Inline Code, Links, and Images.
            Images display like icons, with a max height equal to the font
            height.

            Unsupported Markdown elements are unwrapped so only their children
            (text contents) render. Common block-level Markdown (headings,
            lists, blockquotes) is automatically escaped and displays as
            literal text in labels.

            See the ``body`` parameter of |st.markdown|_ for additional,
            supported Markdown directives.

            .. |st.markdown| replace:: ``st.markdown``
            .. _st.markdown: https://docs.streamlit.io/develop/api-reference/text/st.markdown

        width : "stretch" or int
            The width of the tab container. This can be one of the following:

            - ``"stretch"`` (default): The width of the container matches the
              width of the parent container.
            - An integer specifying the width in pixels: The container has a
              fixed width. If the specified width is greater than the width of
              the parent container, the width of the container matches the width
              of the parent container.

        height : "content", "stretch", or int
            The height of the tab container. This can be one of the following:

            - ``"content"`` (default): The height of the container matches the
              height of its content.
            - ``"stretch"``: The height of the container matches the height
              of the parent container, and content that overflows scrolls
              inside the active tab panel. If the container is not in a
              fixed-height parent, the height of the container matches the
              height of its content.
            - An integer specifying the height in pixels: The container has a
              fixed height. If the content is larger than the specified
              height, scrolling is enabled inside the active tab panel.

            .. note::
                Use scrolling tab panels sparingly. If you use scrolling tab
                panels, avoid heights that exceed 500 pixels. Otherwise, the
                scroll surface of the tab panel might cover the majority of
                the screen on mobile devices, which makes it hard to scroll the
                rest of the app.

        default : str or None
            The default tab to select. If this is ``None`` (default), the first
            tab is selected. If this is a string, it must be one of the tab
            labels. If two tabs have the same label as ``default``, the first
            one is selected.

        key : str, int, or None
            An optional string or integer to use as the unique key for
            the widget. If this is ``None`` (default), a key will be
            generated for the widget based on the values of the other
            parameters. No two widgets may have the same key.

            When ``on_change`` is set to ``"rerun"`` or a callable, setting a
            key lets you read or update the active tab label via
            ``st.session_state[key]``. For more details, see `Widget behavior
            <https://docs.streamlit.io/develop/concepts/architecture/widget-behavior>`_.

            Additionally, if ``key`` is provided, it will be used as a
            CSS class name prefixed with ``st-key-``.

        on_change : "ignore", "rerun", callable, or None
            How the tabs should respond when the user switches tabs. This
            controls whether tabs track state and trigger reruns. ``on_change``
            can be one of the following values:

            - ``"ignore"`` (default): The tabs don't track state. All tab content
              runs regardless of which tab is selected. The ``.open`` attribute
              of each tab container returns ``None`` for all tabs.

            - ``"rerun"``: The tabs track state. Streamlit reruns the app when
              the user switches tabs. The ``.open`` attribute of each tab
              container returns its current state, which is ``True`` if it is
              selected and ``False`` if it isn't selected. This lets you skip
              expensive work in hidden tabs.

            - A callable: The tabs track state. Streamlit executes the callable
              as a callback function and reruns the app when the user switches
              tabs. The ``.open`` attribute of each tab container returns its
              state like when ``on_change="rerun"``. If you need to access
              label of the current tab inside your callback, fetch it through
              Session State.

            When the tabs track state, they can't be used inside
            Streamlit cache-decorated functions.

        args : list or tuple or None
            An optional list or tuple of args to pass to the ``on_change``
            callback.

        kwargs : dict or None
            An optional dict of kwargs to pass to the ``on_change`` callback.

        Returns
        -------
        Sequence of TabContainers
            A sequence of ``TabContainer`` objects with ``.open`` properties to
            return the current state of the tabs if the tabs track state.

        Examples
        --------
        *Example 1: Use context management*

        You can use ``with`` notation to insert any element into a tab:

        .. code-block:: python
            :filename: streamlit_app.py

            import streamlit as st

            tab1, tab2, tab3 = st.tabs(["Cat", "Dog", "Owl"])

            with tab1:
                st.header("A cat")
                st.image("https://static.streamlit.io/examples/cat.jpg", width=200)
            with tab2:
                st.header("A dog")
                st.image("https://static.streamlit.io/examples/dog.jpg", width=200)
            with tab3:
                st.header("An owl")
                st.image("https://static.streamlit.io/examples/owl.jpg", width=200)

        .. output::
            https://doc-tabs1.streamlit.app/
            height: 620px

        *Example 2: Call methods directly*

        You can call methods directly on the returned objects:

        .. code-block:: python
            :filename: streamlit_app.py

            import streamlit as st
            from numpy.random import default_rng as rng

            df = rng(0).standard_normal((10, 1))

            tab1, tab2 = st.tabs(["📈 Chart", "🗃 Data"])

            tab1.subheader("A tab with a chart")
            tab1.line_chart(df)

            tab2.subheader("A tab with the data")
            tab2.write(df)

        .. output::
            https://doc-tabs2.streamlit.app/
            height: 700px

        *Example 3: Set the default tab and style the tab labels*

        Use the ``default`` parameter to set the default tab. You can also use
        Markdown in the tab labels.

        .. code-block:: python
            :filename: streamlit_app.py

            import streamlit as st

            tab1, tab2, tab3 = st.tabs(
                [":cat: Cat", ":dog: Dog", ":rainbow[Owl]"], default=":rainbow[Owl]"
            )

            with tab1:
                st.header("A cat")
                st.image("https://static.streamlit.io/examples/cat.jpg", width=200)
            with tab2:
                st.header("A dog")
                st.image("https://static.streamlit.io/examples/dog.jpg", width=200)
            with tab3:
                st.header("An owl")
                st.image("https://static.streamlit.io/examples/owl.jpg", width=200)

        .. output::
            https://doc-tabs3.streamlit.app/
            height: 620px

        **Example 4: Programmatically control the tab state**

        You can use a key to programmatically control the tab state or access
        the state in callbacks. You must set the ``on_change`` parameter for
        the tabs to track state.

        .. code-block:: python
            :filename: streamlit_app.py

            import streamlit as st


            def switch_tab(tab):
                st.session_state.animal = tab


            def on_tab_change():
                st.toast(f"You opened the {st.session_state.animal} tab.")


            cat, dog, owl = st.tabs(
                ["Cat", "Dog", "Owl"], on_change=on_tab_change, key="animal"
            )

            if cat.open:
                with cat:
                    st.write("This is the cat")
            if dog.open:
                with dog:
                    st.write("This is the dog")
            if owl.open:
                with owl:
                    st.write("This is the owl")

            with st.container(horizontal=True):
                st.button("Cat", on_click=switch_tab, args=("Cat",))
                st.button("Dog", on_click=switch_tab, args=("Dog",))
                st.button("Owl", on_click=switch_tab, args=("Owl",))

        .. output::
            https://doc-tabs-callback.streamlit.app/
            height: 300px

        """
        if not tabs:
            raise StreamlitMissingRequiredParameterError(
                "tabs", detail="Provide at least one tab label."
            )

        if default and default not in tabs:
            raise StreamlitValueError(
                "default",
                ["a tab label from `tabs`"],
                detail=f"`{default}` is not in the list of tabs.",
            )

        for tab in tabs:
            if not isinstance(tab, str):
                raise StreamlitInvalidParameterTypeError(
                    "tabs",
                    type(tab).__name__,
                    ["a string for each tab label"],
                )

        if not callable(on_change) and on_change not in {"ignore", "rerun"}:
            raise StreamlitValueError(
                "on_change",
                ["'rerun'", "'ignore'", "a callback function"],
            )

        key = to_key(key)
        default_index = tabs.index(default) if default else 0
        is_stateful = on_change != "ignore"

        element_id: str | None = None
        block_id: str | None = None
        current_tab_label = tabs[default_index]

        if is_stateful:
            is_callback = callable(on_change)
            check_widget_policies(
                self.dg,
                key,
                on_change=cast("WidgetCallback", on_change)  # ty: ignore[redundant-cast]
                if is_callback
                else None,
                default_value=None,
                writes_allowed=True,
                enable_check_callback_rules=is_callback,
            )

            ctx = get_script_run_ctx()

            element_id = compute_and_register_element_id(
                "tabs",
                user_key=key,
                key_as_main_identity=False,
                dg=self.dg,
                tabs=tuple(tabs),
                width=width,
                height=height,
                default=default,
            )
            block_id = element_id

            serde = _TabsSerde(default_label=tabs[default_index])

            tabs_state = register_widget(
                element_id,
                deserializer=serde.deserialize,
                serializer=serde.serialize,
                ctx=ctx,
                value_type="string_value",
                on_change_handler=on_change if callable(on_change) else None,
                args=args if callable(on_change) else None,
                kwargs=kwargs if callable(on_change) else None,
            )

            current_tab_label = tabs_state.value
            if current_tab_label not in tabs:
                current_tab_label = tabs[default_index]
        elif key is not None:
            block_id = compute_and_register_element_id(
                "tabs",
                user_key=key,
                key_as_main_identity=False,
                dg=self.dg,
            )

        def tab_proto(label: str) -> BlockProto:
            tab_proto = BlockProto()
            tab_proto.tab.label = label
            tab_proto.allow_empty = True
            return tab_proto

        block_proto = BlockProto()
        block_proto.tab_container.SetInParent()
        validate_width(width)
        block_proto.width_config.CopyFrom(get_width_config(width))

        validate_height(height, allow_content=True)
        block_proto.height_config.CopyFrom(get_height_config(height))
        if isinstance(height, int):
            # Ensure the fixed-height tab container renders even when the
            # active tab is empty, so the reserved space is preserved.
            block_proto.allow_empty = True

        # Compute the current tab index from the label
        try:
            current_tab_index = tabs.index(current_tab_label)
        except ValueError:
            current_tab_index = default_index

        block_proto.tab_container.default_tab_index = current_tab_index

        if is_stateful and element_id is not None:
            block_proto.tab_container.id = element_id

        if block_id is not None:
            block_proto.id = block_id

        tab_cls = get_dg_singleton_instance().tab_container_cls
        tab_container = self.dg._block(block_proto)

        tab_dgs: list[TabContainer] = []
        for tab_label in tabs:
            tab_dg = cast(
                "TabContainer",
                tab_container._block(tab_proto(tab_label), dg_type=tab_cls),
            )
            if is_stateful:
                tab_dg.open = tab_label == current_tab_label
            tab_dgs.append(tab_dg)

        return tuple(tab_dgs)
